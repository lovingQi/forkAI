/**
 * Mock jarvis 车端（模拟真实叉车工控机上的 jarvis HTTP/WS 服务）。
 * 仅用于无实车联调 forkAI：实现 gateway 依赖的全部端点，
 * 收到 control 指令打印日志并维护内存假状态，WS 定时推假遥测。
 * 零依赖：Node 内置 http + 手写最小 WebSocket 帧编解码。
 */
import http from 'node:http'
import net from 'node:net'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HOST = process.env.MOCK_JARVIS_HOST || '127.0.0.1'
const PORT = Number(process.env.MOCK_JARVIS_PORT || 8080)
const __dirname = path.dirname(fileURLToPath(import.meta.url))

// 仿真专用地图（真车走 Jarvis 自身 /api/map，不读此文件）
let mapData = null
function loadMapData() {
  const mapPath = process.env.MOCK_MAP_PATH
    ? path.resolve(process.env.MOCK_MAP_PATH)
    : path.join(__dirname, '../maps/umcl-map3.json')
  try {
    mapData = JSON.parse(fs.readFileSync(mapPath, 'utf8'))
    console.log(`【mock车】已加载地图 ${mapPath} Header=${mapData?.Header || ''}`)
  } catch (e) {
    mapData = null
    console.error(`【mock车】地图加载失败 ${mapPath}: ${e.message}`)
  }
}
loadMapData()

let paramsInfo = {}
let paramValues = {}
function loadParams() {
  try {
    paramsInfo = JSON.parse(fs.readFileSync(path.join(__dirname, '../params/params_info.json'), 'utf8'))
    paramValues = JSON.parse(fs.readFileSync(path.join(__dirname, '../params/values.json'), 'utf8'))
    console.log(`【mock车】已加载参数段 ${Object.keys(paramValues).join(',')}`)
  } catch (e) {
    paramsInfo = {}
    paramValues = {}
    console.error(`【mock车】参数加载失败: ${e.message}`)
  }
}
loadParams()

function alarmOf() {
  if (!state.motor) return 'estop'
  return state.alarm
}

function forkInfo() {
  const info = {
    fork_height: state.fork_height,
    sub_type: 2,
    fork_up: 210,
    fork_down: 0,
    flap: true,
    ctrl_flag: true
  }
  if (!state.omitForkDis) {
    info.fork_cur_dis = state.fork_cur_dis
    info.fork_max_dis = state.fork_max_dis
  }
  return info
}

function paramsPayload() {
  const send = {}
  for (const [section, vals] of Object.entries(paramValues)) {
    const origin = paramsInfo[section] || {}
    const sec = {}
    for (const [parameter, def] of Object.entries(vals)) {
      if (origin[parameter]) {
        sec[parameter] = { ...origin[parameter], default: def }
      } else {
        let type = 'string'
        if (typeof def === 'boolean') type = 'bool'
        else if (typeof def === 'number') type = Number.isInteger(def) ? 'int' : 'double'
        sec[parameter] = {
          default: def,
          desc: '',
          desc_zh: '',
          name: '',
          name_zh: '',
          ranges: [],
          type
        }
      }
    }
    send[section] = sec
  }
  return { params: send }
}

function isMoving() {
  if (state.mode !== 'Idle' && state.mode !== 'Stopped') return true
  if (Math.abs(state.vel[0]) > 10 || Math.abs(state.vel[1]) > 10) return true
  return false
}

function applyConfig(data) {
  const cfg = data && data.data ? data.data : data || {}
  if (isMoving()) {
    for (const section of Object.keys(cfg)) {
      if (section.startsWith('laser') || section === 'avoid' || section === 'robot') {
        return { succeed: false, error: `robot is moving, cannot change safety config(${section})` }
      }
    }
  }
  for (const [section, incoming] of Object.entries(cfg)) {
    if (!paramValues[section] || !incoming || typeof incoming !== 'object') {
      return { succeed: false, error: `no found section(${section})` }
    }
    if (section === 'avoid') paramValues[section] = { ...incoming }
    else Object.assign(paramValues[section], incoming)
  }
  return { succeed: true, error: '' }
}

// ---- 内存假车况（对齐真车 /api/state 语义，依据见各注释）----
const state = {
  name: 'fork-01',
  ip: '127.0.0.1',
  robot_type: 'forklift',
  // 真车 mode = 当前 JMode 名（"Idle"/"ModeFocklift"...，JRoutes.h/JModeIdle.cpp），
  // status = "mode,子状态" 组合串（JWebService.cpp:154）
  mode: 'Idle',
  substatus: 'Idle',
  map_name: (mapData && mapData.Header) || 'demo-map',
  score: 0.98,
  battery: 87,
  charing: false,
  ctrl_mode: 0,
  safe: true,
  motor: true,
  alarm: 'normal', // normal | estop | lost | stuck
  // 与仿真地图 PathPoint p1 同坐标系（mm）；无地图时仍可用该演示位姿
  pose: [-11696, -624, 1.57],
  vel: [0, 0, 0],
  fork_height: 120,
  fork_cur_dis: 0,
  fork_max_dis: 640,
  omitForkDis: false,
  speed: 20 // 内部用；真车 /api/state 无 speed 字段（不下发）
}

// 任务 cmd → Mode 名（jarvis 源码构造函数 JMode("...") 字面量）
const MODE_NAMES = {
  focklift: 'ModeFocklift', // src/mode/fork/JModeFocklift.cpp:8
  head: 'ModeHead',
  follow_back: 'ModeFollowBack', // JModeFollowBack.cpp:11
  get_pallet: 'ModeAutoGetPallet', // JModeAutoGetPallet.cpp:8
  charge: 'ModeCharge', // JModeFltCharge.cpp:17
  forkextend: 'ModeFockExtend'
}

// 真车 route 结束（成功/失败/中止相同）后 SetDefaultRICK：routes="TEMP_DEFAULT"、
// key="a"、mode 回默认 Mode "Idle"（libgrm 反汇编：LoopOnce 调 SetDefaultRICK）
const DEFAULT_ROUTES = { routes: 'TEMP_DEFAULT', key: 'a', id: '', status: 'running' }

let forkTimer = null
let moveTimer = null
let driveTimer = null
let headTimer = null
// 动画期间推给 /ws/high 的剩余路径折线；空闲为 []
let pathPointsForHigh = []
// 真车 current_routes.status 恒为 "running"（JWebService.cpp:171 硬编码）
let currentRoutes = { ...DEFAULT_ROUTES }

const MOVE_DURATION_MS = 5000
const MOVE_TICK_MS = 100
const FALLBACK_TASK_MS = 2000
// 仿真点动积分（仅 mock；真车由底盘积分，不读此逻辑）
const DRIVE_TICK_MS = 50
const HEAD_DURATION_MS = 1000
const HEAD_TICK_MS = 50
// 真车口径：trans 即线速度 mm/s（如 500=0.5m/s）；rot±50 为转向幅度
const DRIVE_ANG_AT_ROT50 = 0.5 // rad/s when |rot|=50

function clearDriveTimer() {
  if (driveTimer) {
    clearInterval(driveTimer)
    driveTimer = null
  }
}

function clearHeadTimer() {
  if (headTimer) {
    clearInterval(headTimer)
    headTimer = null
  }
}

function clearMoveTimer() {
  if (moveTimer) {
    clearInterval(moveTimer)
    moveTimer = null
  }
  pathPointsForHigh = []
  state.vel = [0, 0, 0]
}

/** 停掉点动积分（不改 pose；新 route / stop 时用） */
function stopDriveMotion() {
  clearDriveTimer()
  state.vel = [0, 0, 0]
}

function startDriveLoopIfNeeded() {
  if (driveTimer) return
  driveTimer = setInterval(() => {
    const vx = state.vel[0] || 0
    const omega = state.vel[2] || 0
    if (Math.abs(vx) < 1e-6 && Math.abs(omega) < 1e-6) {
      clearDriveTimer()
      return
    }
    const dt = DRIVE_TICK_MS / 1000
    const th = state.pose[2]
    state.pose[0] += vx * Math.cos(th) * dt
    state.pose[1] += vx * Math.sin(th) * dt
    state.pose[2] = th + omega * dt
  }, DRIVE_TICK_MS)
}

function routeEnd() {
  currentRoutes = { ...DEFAULT_ROUTES }
  state.mode = 'Idle'
  state.substatus = 'Idle'
}

function routeBegin(name, cmd) {
  // 真车语义：新 route 启动会 Stop 旧任务（JRoutes.h:54 注释）
  // —— 清理未完成的叉高渐变 / 路径动画 / 点动积分 / head 旋转
  if (forkTimer) {
    clearInterval(forkTimer)
    forkTimer = null
  }
  clearMoveTimer()
  clearHeadTimer()
  stopDriveMotion()
  currentRoutes = { routes: name, key: 'a', id: '', status: 'running' }
  state.mode = MODE_NAMES[cmd] || 'Idle'
  state.substatus = MODE_NAMES[cmd] || ''
}

/** head：约 1s 平滑转到目标角（度→弧度）；mock_fail 不改角 */
function startHeadSim(name, node) {
  const fail = !!node.mock_fail
  const angleDeg = Number(node.angle || 0)
  routeBegin(name, 'head')
  state.substatus = 'head'
  console.log(`【mock车】head 原地旋转 ${angleDeg}度 fail=${fail}`)

  if (fail) {
    setTimeout(() => {
      routeEnd()
      console.log(`【mock车】head 结束 pose[2]=${state.pose[2].toFixed(3)}rad fail=true`)
    }, HEAD_DURATION_MS)
    return
  }

  const startTh = state.pose[2]
  const delta = (angleDeg * Math.PI) / 180
  const targetTh = startTh + delta
  const ticks = Math.max(1, Math.round(HEAD_DURATION_MS / HEAD_TICK_MS))
  let step = 0
  clearHeadTimer()
  headTimer = setInterval(() => {
    step += 1
    if (step >= ticks) {
      state.pose[2] = targetTh
      clearHeadTimer()
      routeEnd()
      console.log(`【mock车】head 结束 pose[2]=${state.pose[2].toFixed(3)}rad fail=false`)
      return
    }
    const t = step / ticks
    state.pose[2] = startTh + delta * t
  }, HEAD_TICK_MS)
}

// ---- 仿真路径动画（仅 mock；真车由 Jarvis 自身导航，不走此逻辑）----

function parsePoseXY(poseStr) {
  if (!poseStr) return { x: 0, y: 0 }
  const p = String(poseStr).trim().split(/\s+/).map((v) => parseFloat(v))
  return { x: p[0] || 0, y: p[1] || 0 }
}

function buildPathGraph(data) {
  const pts = {}
  const adj = {}
  const raw = []
  const list = data && data.Objs && Array.isArray(data.Objs.PathPoint) ? data.Objs.PathPoint : []
  for (const pp of list) {
    if (!pp || !pp.name) continue
    raw.push(pp)
    const xy = parsePoseXY(pp.pose)
    pts[pp.name] = xy
    if (!adj[pp.name]) adj[pp.name] = new Set()
  }
  for (const pp of raw) {
    const a = pp.name
    for (const con of pp.connections || []) {
      if (!con || !con.name || !pts[con.name]) continue
      adj[a].add(con.name)
      if (!adj[con.name]) adj[con.name] = new Set()
      adj[con.name].add(a)
    }
  }
  const adjList = {}
  for (const [k, s] of Object.entries(adj)) adjList[k] = [...s]
  return { pts, adj: adjList, raw }
}

function normalizeMapNodeName(name, pts) {
  if (name == null) return null
  const s = String(name).trim()
  if (!s) return null
  const keys = Object.keys(pts)
  const lower = {}
  for (const k of keys) lower[k.toLowerCase()] = k
  const candidates = [s]
  if (s.endsWith('站点') && s.length > 2) candidates.push(s.slice(0, -2))
  else if (s.endsWith('点')) candidates.push(s.slice(0, -1))
  for (const c of candidates) {
    if (pts[c]) return c
    const hit = lower[c.toLowerCase()]
    if (hit) return hit
  }
  return null
}

function bfsShortestPath(adj, start, goal) {
  if (!start || !goal || !adj[start] || !adj[goal]) return null
  if (start === goal) return [start]
  const q = [[start]]
  const seen = new Set([start])
  while (q.length) {
    const path = q.shift()
    const u = path[path.length - 1]
    for (const v of adj[u] || []) {
      if (seen.has(v)) continue
      const next = path.concat(v)
      if (v === goal) return next
      seen.add(v)
      q.push(next)
    }
  }
  return null
}

function findConnection(raw, fromName, toName) {
  const pp = raw.find((p) => p && p.name === fromName)
  if (!pp || !Array.isArray(pp.connections)) return null
  return pp.connections.find((c) => c && c.name === toName) || null
}

function sampleCubic(a, c1, c2, b, n) {
  const out = []
  const steps = Math.max(2, n)
  for (let i = 0; i <= steps; i++) {
    const t = i / steps
    const u = 1 - t
    const x = u * u * u * a.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t * t * t * b.x
    const y = u * u * u * a.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t * t * t * b.y
    out.push({ x, y })
  }
  return out
}

function sampleLine(a, b, n) {
  const out = []
  const steps = Math.max(1, n)
  for (let i = 0; i <= steps; i++) {
    const t = i / steps
    out.push({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t })
  }
  return out
}

function edgeSamples(aName, bName, pts, raw) {
  const a = pts[aName]
  const b = pts[bName]
  if (!a || !b) return []
  let con = findConnection(raw, aName, bName)
  let reverse = false
  if (!con) {
    con = findConnection(raw, bName, aName)
    reverse = !!con
  }
  const segLen = Math.hypot(b.x - a.x, b.y - a.y) || 1
  const n = Math.max(8, Math.ceil(segLen / 80))
  let samples
  if (con && Number(con.type) === 1) {
    // 与 CanvasView 一致：控制点相对「连接所在节点」；正向相对 A，反向相对 B 再反转
    if (!reverse) {
      const c1 = { x: a.x + (Number(con.x1) || 0), y: a.y + (Number(con.y1) || 0) }
      const c2 = { x: a.x + (Number(con.x2) || 0), y: a.y + (Number(con.y2) || 0) }
      samples = sampleCubic(a, c1, c2, b, n)
    } else {
      const c1 = { x: b.x + (Number(con.x1) || 0), y: b.y + (Number(con.y1) || 0) }
      const c2 = { x: b.x + (Number(con.x2) || 0), y: b.y + (Number(con.y2) || 0) }
      samples = sampleCubic(b, c1, c2, a, n).reverse()
    }
  } else {
    samples = sampleLine(a, b, n)
  }
  return samples
}

function buildRoutePolyline(nodeNames, pts, raw) {
  if (!nodeNames || nodeNames.length === 0) return []
  if (nodeNames.length === 1) {
    const p = pts[nodeNames[0]]
    return p ? [{ x: p.x, y: p.y }] : []
  }
  const poly = []
  for (let i = 0; i < nodeNames.length - 1; i++) {
    const seg = edgeSamples(nodeNames[i], nodeNames[i + 1], pts, raw)
    if (!seg.length) continue
    if (poly.length) seg.shift() // 去重相邻段接点
    for (const p of seg) poly.push(p)
  }
  return poly
}

function resampleByArcLength(poly, count) {
  if (!poly.length) return []
  if (poly.length === 1 || count <= 1) return [poly[0]]
  const dist = [0]
  for (let i = 1; i < poly.length; i++) {
    dist[i] =
      dist[i - 1] + Math.hypot(poly[i].x - poly[i - 1].x, poly[i].y - poly[i - 1].y)
  }
  const total = dist[dist.length - 1] || 1
  const out = []
  for (let i = 0; i < count; i++) {
    const target = (total * i) / (count - 1)
    let j = 1
    while (j < dist.length && dist[j] < target) j++
    const d0 = dist[j - 1]
    const d1 = dist[j] || d0 + 1
    const t = (target - d0) / (d1 - d0 || 1)
    const a = poly[j - 1]
    const b = poly[j] || a
    out.push({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t })
  }
  return out
}

function fallbackTaskEnd(name, cmd, fail, extraLog) {
  console.log(
    `【mock车】${cmd} 模拟开始 name=${name} fallback=${extraLog || 'short'} fail=${fail}`
  )
  setTimeout(() => {
    routeEnd()
    console.log(`【mock车】${cmd} 模拟结束 fail=${fail}`)
  }, FALLBACK_TASK_MS)
}

/** follow_back：地图 p1…p5 最短路径约 5s 动画；未知站点回退 2s 无位移（保 week 回归） */
function startFollowBackMove(name, node) {
  const fail = !!node.mock_fail
  routeBegin(name, 'follow_back')
  state.substatus = 'follow_back'

  if (fail) {
    fallbackTaskEnd(name, 'follow_back', true, 'mock_fail')
    return
  }

  const graph = buildPathGraph(mapData)
  const start = normalizeMapNodeName(node.start_name, graph.pts)
  const goal = normalizeMapNodeName(node.target_name, graph.pts)
  const hopPath = start && goal ? bfsShortestPath(graph.adj, start, goal) : null

  if (!hopPath || hopPath.length === 0) {
    fallbackTaskEnd(
      name,
      'follow_back',
      false,
      `no_path start=${node.start_name} target=${node.target_name}`
    )
    return
  }

  const rawPoly = buildRoutePolyline(hopPath, graph.pts, graph.raw)
  const ticks = Math.max(2, Math.round(MOVE_DURATION_MS / MOVE_TICK_MS))
  const samples = resampleByArcLength(rawPoly, ticks + 1)
  if (samples.length < 2) {
    fallbackTaskEnd(name, 'follow_back', false, 'empty_poly')
    return
  }

  // 对齐路径起点；yaw 为弧度，沿切线
  state.pose[0] = samples[0].x
  state.pose[1] = samples[0].y
  const d0x = samples[1].x - samples[0].x
  const d0y = samples[1].y - samples[0].y
  if (d0x !== 0 || d0y !== 0) state.pose[2] = Math.atan2(d0y, d0x)

  console.log(
    `【mock车】follow_back 路径动画 name=${name} path=${hopPath.join('→')} duration=${MOVE_DURATION_MS}ms`
  )

  let step = 0
  clearMoveTimer()
  // clearMoveTimer 会清空 pathPoints/vel，需重新写入
  pathPointsForHigh = samples.map((p) => ({ x: p.x, y: p.y }))
  state.vel = [0.3, 0, 0]

  // 每 tick 最多转这么多弧度，避免弯道硬切（约 180°/s @100ms）
  const YAW_MAX_STEP = 0.35
  const YAW_LERP = 0.35

  function shortestAngleDelta(from, to) {
    let d = to - from
    while (d > Math.PI) d -= Math.PI * 2
    while (d < -Math.PI) d += Math.PI * 2
    return d
  }

  function smoothYawToward(target) {
    const cur = state.pose[2]
    let delta = shortestAngleDelta(cur, target)
    // 指数逼近 + 单步上限
    delta *= YAW_LERP
    if (delta > YAW_MAX_STEP) delta = YAW_MAX_STEP
    if (delta < -YAW_MAX_STEP) delta = -YAW_MAX_STEP
    state.pose[2] = cur + delta
  }

  moveTimer = setInterval(() => {
    step += 1
    if (step >= samples.length) {
      const last = samples[samples.length - 1]
      state.pose[0] = last.x
      state.pose[1] = last.y
      clearMoveTimer()
      routeEnd()
      console.log(
        `【mock车】follow_back 到位 path=${hopPath.join('→')} pose=${state.pose[0].toFixed(1)},${state.pose[1].toFixed(1)}`
      )
      return
    }
    const cur = samples[step]
    const prev = samples[step - 1]
    state.pose[0] = cur.x
    state.pose[1] = cur.y
    const dx = cur.x - prev.x
    const dy = cur.y - prev.y
    if (dx !== 0 || dy !== 0) smoothYawToward(Math.atan2(dy, dx))
    pathPointsForHigh = samples.slice(step).map((p) => ({ x: p.x, y: p.y }))
    state.vel = [0.3, 0, 0]
  }, MOVE_TICK_MS)
}

/** 模拟叉高渐变：每 200ms 向目标 pos 步进 10mm。mock_fail:true 时中途停（目标不达）。 */
function startForkSim(name, node) {
  const target = Number(node.pos || 0)
  const fail = !!node.mock_fail
  if (forkTimer) clearInterval(forkTimer)
  routeBegin(name, 'focklift')
  state.substatus = `叉高调整中 ${target}`
  console.log(`【mock车】货叉路线启动 name=${name} 目标=${target}mm fail=${fail}`)
  let steps = 0
  forkTimer = setInterval(() => {
    const diff = target - state.fork_height
    steps += 1
    // mock_fail：走 3 步即中止（模拟任务失败：物理量未达标）
    if (fail && steps >= 3) {
      clearInterval(forkTimer)
      forkTimer = null
      routeEnd()
      console.log(`【mock车】货叉失败中止 fork_height=${state.fork_height}（目标 ${target}）`)
      return
    }
    if (Math.abs(diff) <= 10) {
      state.fork_height = target
      clearInterval(forkTimer)
      forkTimer = null
      routeEnd()
      console.log(`【mock车】货叉到位 ${target}mm`)
    } else {
      state.fork_height += Math.sign(diff) * 10
      console.log(`【mock车】叉高 ${state.fork_height}mm → ${target}mm`)
    }
  }, 200)
}

/** 模拟货叉伸缩：每 200ms 向目标步进 10mm。 */
function startForkExtendSim(name, node) {
  const target = Number(node.pos || 0)
  if (forkTimer) clearInterval(forkTimer)
  routeBegin(name, 'forkextend')
  state.substatus = `叉尺调整中 ${target}`
  console.log(`【mock车】货叉伸缩 name=${name} 目标=${target}mm`)
  forkTimer = setInterval(() => {
    const diff = target - state.fork_cur_dis
    if (Math.abs(diff) <= 10) {
      state.fork_cur_dis = target
      clearInterval(forkTimer)
      forkTimer = null
      routeEnd()
      console.log(`【mock车】叉尺到位 ${target}mm`)
    } else {
      state.fork_cur_dis += Math.sign(diff) * 10
    }
  }, 200)
}

/** head/follow_back/get_pallet/charge 节点模拟。mock_fail:true → 无物理效果直接结束。 */
function startTaskSim(name, node) {
  const cmd = node.cmd
  const fail = !!node.mock_fail
  if (cmd === 'follow_back') {
    startFollowBackMove(name, node)
    return
  }
  if (cmd === 'head') {
    startHeadSim(name, node)
    return
  }
  routeBegin(name, cmd)
  state.substatus = cmd
  console.log(`【mock车】${cmd} 模拟开始 name=${name} payload=${JSON.stringify(node)}`)
  setTimeout(() => {
    if (cmd === 'charge' && !fail) state.charing = true
    routeEnd()
    if (cmd === 'charge' && !fail) state.substatus = '充电中'
    console.log(`【mock车】${cmd} 模拟结束 fail=${fail}`)
  }, FALLBACK_TASK_MS)
}

function lowPayload() {
  return {
    type: 'low',
    name: state.name,
    ip: state.ip,
    robot_type: state.robot_type,
    mode: state.mode,
    status: `${state.mode},${state.substatus}`, // 真车: status="mode,status"（JWebService.cpp:154）
    map_name: state.map_name,
    score: state.score,
    battery: state.battery,
    charing: state.charing,
    ctrl_mode: state.ctrl_mode,
    safe: state.safe,
    motor: state.motor,
    alarm: alarmOf(),
    // 注意：真车 /api/state 无 speed 字段
    current_routes: currentRoutes,
    fork_info: forkInfo(),
    input: [],
    output: [],
    virtual: []
  }
}

function laserPoints(pts) {
  return pts.map(([px, py]) => `${Math.round(px)} ${Math.round(py)}`).join(' ') + ' '
}

function laserData() {
  const [x, y] = state.pose
  const groups = [
    {
      name: 'laser',
      pts: [
        [x + 800, y + 200],
        [x + 900, y],
        [x + 800, y - 200]
      ]
    },
    {
      name: 'laser_back',
      pts: [
        [x - 700, y + 150],
        [x - 800, y],
        [x - 700, y - 150]
      ]
    },
    {
      name: 'sonar',
      pts: [
        [x + 400, y + 350],
        [x + 400, y - 350]
      ]
    }
  ]
  const data = groups.map((g) => ({
    name: g.name,
    num: g.pts.length,
    points: laserPoints(g.pts)
  }))
  return { size: data.length, data }
}

function highPayload() {
  return {
    type: 'high',
    vel: state.vel.join(','),
    pose: state.pose.join(','),
    laser_data: laserData(),
    path_points: { points: pathPointsForHigh },
    clearances: { points: [] },
    // 与 CanvasView 一致：毫米口径（非米）
    robot_size: { width: 900, length: 2200, length_front: 1200, length_rear: 1000 }
  }
}

// GET /api/state 返回合并视图（executor 查询与前端 loadInitial 都用它）
function statePayload() {
  return {
    ...lowPayload(),
    pose: state.pose.join(','),
    vel: state.vel.join(','),
    laser_data: laserData()
  }
}

// ---- control 指令处理（真车控制端点统一响应 {"succeed":true}）----
function handleControl(action, payload) {
  const p = payload || {}
  console.log(`【mock车】收到指令 action=${action} payload=${JSON.stringify(p)}`)
  switch (action) {
    case 'drive': {
      if (!state.motor) break
      // 真车：trans=mm/s，rot=转向幅度，speed 恒定；mode 保持 Idle
      const trans = Number(p.trans || 0)
      const rot = Number(p.rot || 0)
      state.speed = Number(p.speed || state.speed)
      state.substatus = trans > 0 ? '前进' : trans < 0 ? '后退' : rot !== 0 ? '转向' : '点动'
      const vx = trans // mm/s
      const omega = (rot / 50) * DRIVE_ANG_AT_ROT50 // rad/s
      state.vel = [vx, 0, omega]
      if (Math.abs(vx) < 1e-6 && Math.abs(omega) < 1e-6) {
        clearDriveTimer()
      } else {
        startDriveLoopIfNeeded()
      }
      break
    }
    case 'stop':
      if (forkTimer) {
        clearInterval(forkTimer)
        forkTimer = null
      }
      clearMoveTimer()
      clearHeadTimer()
      stopDriveMotion()
      currentRoutes = { ...DEFAULT_ROUTES }
      state.mode = 'Idle'
      state.substatus = 'Idle'
      state.vel = [0, 0, 0]
      state.charing = false
      break
    case 'motor':
      state.motor = !(p.flag === false || p.flag === 0)
      if (!state.motor) state.alarm = 'estop'
      else if (state.alarm === 'estop') state.alarm = 'normal'
      break
    case 'safe':
      state.safe = !(p.flag === false || p.flag === 0)
      break
    case 'idle':
      clearHeadTimer()
      stopDriveMotion()
      state.mode = 'Idle'
      state.substatus = '待机'
      state.vel = [0, 0, 0]
      state.charing = false
      break
    case 'dock':
      // 真车 dock：车端自己找最近 Dock 点发 charge/steer_charge → ModeCharge
      state.mode = 'ModeCharge'
      state.substatus = '回充中'
      state.vel = [0, 0, 0]
      break
    case 'goto':
      state.mode = 'ModeGoto'
      state.substatus = `前往${p.goal || p.target || '目标点'}`
      break
    case 'scheduler':
    case 'routes': {
      // 内联路线（真车结构）：{name, content:{a:{cmd,...}}}；命名路线 {routes,key,id}
      const content = p.content && typeof p.content === 'object' ? p.content : {}
      const nodes = Object.values(content).filter((v) => v && typeof v === 'object' && v.cmd)
      const name = String(p.name || p.routes || action)
      const forkNode = nodes.find((n) => n.cmd === 'focklift')
      const extendNode = nodes.find((n) => n.cmd === 'forkextend')
      const taskNode = nodes.find((n) =>
        ['head', 'follow_back', 'get_pallet', 'charge'].includes(n.cmd)
      )
      if (forkNode) {
        startForkSim(name, forkNode)
      } else if (extendNode) {
        startForkExtendSim(name, extendNode)
      } else if (taskNode) {
        startTaskSim(name, taskNode)
      } else {
        console.log(`【mock车】未模拟的route节点 payload=${JSON.stringify(p)}`)
      }
      break
    }
    case 'schedulerthis':
      console.log('【mock车】警告：schedulerthis 已废弃（真车 404），请用 /api/control/scheduler')
      break
    default:
      console.log(`【mock车】未识别 action=${action}，仍返回成功`)
  }
  // 真车控制类端点统一只回 {"succeed":true}（无执行结果反馈）
  return { succeed: true }
}

// ---- HTTP ----
function sendJson(res, code, obj) {
  const body = JSON.stringify(obj)
  res.writeHead(code, {
    'Content-Type': 'application/json; charset=utf-8',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    'Access-Control-Allow-Methods': 'GET,POST,OPTIONS'
  })
  res.end(body)
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`)
  const path = url.pathname

  if (req.method === 'OPTIONS') return sendJson(res, 200, {})

  if (req.method === 'GET' && path === '/api/state') return sendJson(res, 200, statePayload())
  if (req.method === 'GET' && path === '/api/map') {
    return sendJson(res, 200, { name: state.map_name, data: mapData })
  }
  if (req.method === 'GET' && path === '/api/params') return sendJson(res, 200, paramsPayload())

  if (req.method === 'POST' && path === '/api/config') {
    let body = ''
    req.on('data', (c) => (body += c))
    req.on('end', () => {
      let payload = {}
      try {
        payload = body ? JSON.parse(body) : {}
      } catch {
        /* ignore */
      }
      sendJson(res, 200, applyConfig(payload))
    })
    return
  }

  // 调试端点（仅供联调测试）：直接改内存状态，模拟低电量/告警/充电等场景
  if (req.method === 'POST' && path === '/api/debug/state') {
    let body = ''
    req.on('data', (c) => (body += c))
    req.on('end', () => {
      let p = {}
      try {
        p = body ? JSON.parse(body) : {}
      } catch {
        /* ignore */
      }
      if (p.battery !== undefined) state.battery = Number(p.battery)
      if (p.alarm !== undefined) state.alarm = String(p.alarm)
      if (p.charing !== undefined) state.charing = !!p.charing
      if (p.motor !== undefined) state.motor = !!p.motor
      if (p.omitForkDis !== undefined) state.omitForkDis = !!p.omitForkDis
      if (p.fork_cur_dis !== undefined) state.fork_cur_dis = Number(p.fork_cur_dis)
      if (p.fork_height !== undefined) state.fork_height = Number(p.fork_height)
      console.log(`【mock车】debug/state battery=${state.battery} alarm=${state.alarm} charing=${state.charing}`)
      sendJson(res, 200, {
        succeed: true,
        battery: state.battery,
        alarm: state.alarm,
        charing: state.charing
      })
    })
    return
  }

  if (req.method === 'POST' && path.startsWith('/api/control/')) {
    const action = decodeURIComponent(path.slice('/api/control/'.length))
    let body = ''
    req.on('data', (c) => (body += c))
    req.on('end', () => {
      let payload = {}
      try {
        payload = body ? JSON.parse(body) : {}
      } catch {
        /* ignore */
      }
      sendJson(res, 200, handleControl(action, payload))
    })
    return
  }

  sendJson(res, 404, { succeed: false, error: 'not_found', path })
})

// ---- 最小 WebSocket 服务端（仅服务端发文本帧 + 收 close/ping）----
const WS_MAGIC = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

function wsAccept(key) {
  return crypto.createHash('sha1').update(key + WS_MAGIC).digest('base64')
}

function encodeTextFrame(str) {
  const payload = Buffer.from(str, 'utf8')
  const len = payload.length
  let header
  if (len < 126) {
    header = Buffer.from([0x81, len])
  } else if (len < 65536) {
    header = Buffer.alloc(4)
    header[0] = 0x81
    header[1] = 126
    header.writeUInt16BE(len, 2)
  } else {
    header = Buffer.alloc(10)
    header[0] = 0x81
    header[1] = 127
    header.writeBigUInt64BE(BigInt(len), 2)
  }
  return Buffer.concat([header, payload])
}

server.on('upgrade', (req, socket) => {
  const url = req.url || ''
  if (!url.startsWith('/ws/high') && !url.startsWith('/ws/low')) {
    socket.destroy()
    return
  }
  const key = req.headers['sec-websocket-key']
  if (!key) {
    socket.destroy()
    return
  }
  socket.write(
    'HTTP/1.1 101 Switching Protocols\r\n' +
      'Upgrade: websocket\r\n' +
      'Connection: Upgrade\r\n' +
      `Sec-WebSocket-Accept: ${wsAccept(key)}\r\n\r\n`
  )
  socket.setNoDelay(true)

  const kind = url.startsWith('/ws/high') ? 'high' : 'low'
  console.log(`【mock车】WS /ws/${kind} 客户端已连接`)
  // 真车推送频率：high 10Hz / low 1Hz；入站数据忽略
  const timer = setInterval(() => {
    const frame = encodeTextFrame(JSON.stringify(kind === 'high' ? highPayload() : lowPayload()))
    if (!socket.destroyed) socket.write(frame)
  }, kind === 'high' ? 100 : 1000)

  socket.on('data', () => {})
  socket.on('close', () => clearInterval(timer))
  socket.on('error', () => clearInterval(timer))
})

server.listen(PORT, HOST, () => {
  console.log(`[mock-jarvis] http://${HOST}:${PORT} 已就绪（模拟车端，无实车）`)
})

// 建图录包的 TCP 模拟。帧格式：#$<长度>##<JSON>~$。核对点 G 未对照真车。
const TCP_PORT = Number(process.env.MOCK_JARVIS_TCP_PORT || 7273)
let scanName = ''
const tcp = net.createServer((socket) => {
  let buf = ''
  socket.on('data', (chunk) => {
    buf += chunk.toString('utf8')
    while (true) {
      const head = buf.indexOf('#$')
      if (head < 0) return
      const mid = buf.indexOf('##', head + 2)
      if (mid < 0) return
      const len = Number(buf.slice(head + 2, mid))
      if (!Number.isFinite(len)) { buf = ''; return }
      const start = mid + 2
      if (buf.length < start + len + 2) return
      const body = buf.slice(start, start + len)
      buf = buf.slice(start + len + 2)
      let packet = {}
      try { packet = JSON.parse(body) } catch { packet = {} }
      const cmd = packet['#CMD#']
      let reply = { '#CMD#': cmd, ok: true }
      if (cmd === 'UmConnect') {
        reply.ok = packet.user === 'test' && packet.password === 'test'
      } else if (cmd === 'UmMapping') {
        scanName = packet.flag ? String(packet.name || '') : ''
      } else if (cmd === 'UmGetMappingState') {
        reply.flag = scanName !== ''
        reply.name = scanName
      }
      const text = JSON.stringify(reply)
      socket.write(`#$${text.length}##${text}~$`)
    }
  })
})
tcp.listen(TCP_PORT, HOST, () => {
  console.log(`[mock-jarvis] tcp://${HOST}:${TCP_PORT} 建图录包模拟`)
})
