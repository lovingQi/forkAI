/**
 * Mock jarvis 车端（模拟真实叉车工控机上的 jarvis HTTP/WS 服务）。
 * 仅用于无实车联调 forkAI：实现 gateway 依赖的全部端点，
 * 收到 control 指令打印日志并维护内存假状态，WS 定时推假遥测。
 * 零依赖：Node 内置 http + 手写最小 WebSocket 帧编解码。
 */
import http from 'node:http'
import crypto from 'node:crypto'

const HOST = process.env.MOCK_JARVIS_HOST || '127.0.0.1'
const PORT = Number(process.env.MOCK_JARVIS_PORT || 8080)

// ---- 内存假车况（对齐真车 /api/state 语义，依据见各注释）----
const state = {
  name: 'fork-01',
  ip: '127.0.0.1',
  robot_type: 'forklift',
  // 真车 mode = 当前 JMode 名（"Idle"/"ModeFocklift"...，JRoutes.h/JModeIdle.cpp），
  // status = "mode,子状态" 组合串（JWebService.cpp:154）
  mode: 'Idle',
  substatus: 'Idle',
  map_name: 'demo-map',
  score: 0.98,
  battery: 87,
  charing: false,
  ctrl_mode: 0,
  safe: true,
  motor: true,
  alarm: 'normal', // normal | estop | lost | stuck
  pose: [12.5, 3.2, 1.57],
  vel: [0, 0, 0],
  fork_height: 120,
  speed: 20 // 内部用；真车 /api/state 无 speed 字段（不下发）
}

// 任务 cmd → Mode 名（jarvis 源码构造函数 JMode("...") 字面量）
const MODE_NAMES = {
  focklift: 'ModeFocklift', // src/mode/fork/JModeFocklift.cpp:8
  head: 'ModeHead',
  follow_back: 'ModeFollowBack', // JModeFollowBack.cpp:11
  get_pallet: 'ModeAutoGetPallet', // JModeAutoGetPallet.cpp:8
  charge: 'ModeCharge' // JModeFltCharge.cpp:17
}

// 真车 route 结束（成功/失败/中止相同）后 SetDefaultRICK：routes="TEMP_DEFAULT"、
// key="a"、mode 回默认 Mode "Idle"（libgrm 反汇编：LoopOnce 调 SetDefaultRICK）
const DEFAULT_ROUTES = { routes: 'TEMP_DEFAULT', key: 'a', id: '', status: 'running' }

let forkTimer = null
// 真车 current_routes.status 恒为 "running"（JWebService.cpp:171 硬编码）
let currentRoutes = { ...DEFAULT_ROUTES }

function routeEnd() {
  currentRoutes = { ...DEFAULT_ROUTES }
  state.mode = 'Idle'
  state.substatus = 'Idle'
}

function routeBegin(name, cmd) {
  // 真车语义：新 route 启动会 Stop 旧任务（JRoutes.h:54 注释）
  // —— 清理未完成的叉高渐变，防止旧定时器 routeEnd 覆盖新 route 的 current_routes
  if (forkTimer) {
    clearInterval(forkTimer)
    forkTimer = null
  }
  currentRoutes = { routes: name, key: 'a', id: '', status: 'running' }
  state.mode = MODE_NAMES[cmd] || 'Idle'
  state.substatus = MODE_NAMES[cmd] || ''
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

/** head/follow_back/get_pallet/charge 节点模拟。mock_fail:true → 无物理效果直接结束。 */
function startTaskSim(name, node) {
  const cmd = node.cmd
  const fail = !!node.mock_fail
  routeBegin(name, cmd)
  state.substatus = cmd
  if (cmd === 'head') {
    console.log(`【mock车】head 原地旋转 ${node.angle}度 fail=${fail}`)
    setTimeout(() => {
      if (!fail) state.pose[2] += (Number(node.angle || 0) * Math.PI) / 180
      routeEnd()
      console.log(`【mock车】head 结束 pose[2]=${state.pose[2].toFixed(3)}rad fail=${fail}`)
    }, 1000)
    return
  }
  console.log(`【mock车】${cmd} 模拟开始 name=${name} payload=${JSON.stringify(node)}`)
  setTimeout(() => {
    if (cmd === 'charge' && !fail) state.charing = true
    routeEnd()
    if (cmd === 'charge' && !fail) state.substatus = '充电中'
    console.log(`【mock车】${cmd} 模拟结束 fail=${fail}`)
  }, 2000)
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
    alarm: state.alarm,
    // 注意：真车 /api/state 无 speed 字段
    current_routes: currentRoutes,
    fork_info: { fork_height: state.fork_height },
    input: [],
    output: [],
    virtual: []
  }
}

function highPayload() {
  return {
    type: 'high',
    vel: state.vel.join(','),
    pose: state.pose.join(','),
    laser_data: { data: [] },
    path_points: { points: [] },
    clearances: { points: [] },
    robot_size: { width: 0.9, length: 2.2, length_front: 1.2, length_rear: 1.0 }
  }
}

// GET /api/state 返回合并视图（executor 查询与前端 loadInitial 都用它）
function statePayload() {
  return {
    ...lowPayload(),
    pose: state.pose.join(','),
    vel: state.vel.join(',')
  }
}

// ---- control 指令处理（真车控制端点统一响应 {"succeed":true}）----
function handleControl(action, payload) {
  const p = payload || {}
  console.log(`【mock车】收到指令 action=${action} payload=${JSON.stringify(p)}`)
  switch (action) {
    case 'drive': {
      // 真车 JWebService::Drive 不切 JRoutes mode（mode 保持 Idle），仅改速度
      const trans = Number(p.trans || 0)
      const rot = Number(p.rot || 0)
      state.speed = Number(p.speed || state.speed)
      state.substatus = trans > 0 ? '前进' : trans < 0 ? '后退' : rot !== 0 ? '转向' : '点动'
      state.vel = [trans * (state.speed / 100), 0, rot * 0.5]
      break
    }
    case 'stop':
      if (forkTimer) {
        clearInterval(forkTimer)
        forkTimer = null
      }
      currentRoutes = { ...DEFAULT_ROUTES }
      state.mode = 'Idle'
      state.substatus = 'Idle'
      state.vel = [0, 0, 0]
      state.charing = false
      break
    case 'idle':
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
      const taskNode = nodes.find((n) =>
        ['head', 'follow_back', 'get_pallet', 'charge'].includes(n.cmd)
      )
      if (forkNode) {
        startForkSim(name, forkNode)
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
    return sendJson(res, 200, { name: state.map_name, data: null })
  }
  if (req.method === 'GET' && path === '/api/params') return sendJson(res, 200, { params: {} })

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
