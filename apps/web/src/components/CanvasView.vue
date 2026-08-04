<template>
  <div ref="wrap" class="canvas-wrap">
    <canvas ref="cv" class="canvas-el"></canvas>
    <div class="canvas-toolbar">
      <el-button size="small" @click="recenter">回中</el-button>
      <el-button size="small" @click="zoom(1.2)">放大</el-button>
      <el-button size="small" @click="zoom(1 / 1.2)">缩小</el-button>
      <el-tag size="small" type="info">{{ (scale * 1000).toFixed(1) }} px/m</el-tag>
    </div>
    <div class="canvas-legend">
      <div v-for="(c, i) in LASER_COLORS" :key="i" class="legend-item">
        <span class="legend-swatch" :style="{ background: c }"></span>
        <span>组{{ i + 1 }}</span>
      </div>
    </div>
    <div
      v-if="ctxMenu.visible"
      class="ctx-menu"
      :style="{ left: ctxMenu.x + 'px', top: ctxMenu.y + 'px' }"
    >
      <div class="ctx-menu-item" @click="onAutoDrive">到达 {{ ctxMenu.name }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRobotStore, type Point } from '@/stores/robot'
import { control } from '@/api/http'

export interface ReplayPoint extends Point {
  timeMs?: number
  frameIndex?: number
  timestamp?: string
  title?: string
  level?: string
}

const props = withDefaults(
  defineProps<{ showMap?: boolean; showAvoidBox?: boolean; trajectory?: ReplayPoint[]; eventPoints?: ReplayPoint[] }>(),
  {
    showMap: true,
    showAvoidBox: false,
    trajectory: () => [],
    eventPoints: () => []
  }
)
const emit = defineEmits<{ (e: 'select-replay-point', point: ReplayPoint): void }>()

const store = useRobotStore()

// 激光分组固定配色(按组序号取色)
const LASER_COLORS = ['#f87171', '#fbbf24', '#c084fc', '#f472b6', '#22d3ee', '#38bdf8']

// 路网拓扑配色/尺寸
const PATH_STRAIGHT_COLOR = 'rgba(96,165,250,0.35)' // 直线连接
const PATH_BEZIER_COLOR = '#e879f9' // 贝塞尔曲线(醒目区分)
const PATH_NODE_COLOR = '#60a5fa' // 路径点方块
const PATH_NODE_TEXT_COLOR = '#cbd5e1' // 路径点名称
const PATH_NODE_SIZE = 6 // 方块边长(px, 固定)
const PATH_LABEL_MIN_SCALE = 0.06 // 名称显示的最小缩放(px/mm)

const wrap = ref<HTMLDivElement | null>(null)
const cv = ref<HTMLCanvasElement | null>(null)
let ctx: CanvasRenderingContext2D | null = null
let raf = 0
let ro: ResizeObserver | null = null

// 视图状态：scale 为 px/mm，pan 为附加在跟随中心上的世界偏移(mm)
// 初始分辨率约 35px/m(0.035 px/mm)，以机器人为中心，其余区域靠拖动查看
const INIT_SCALE = 0.035
const scale = ref(INIT_SCALE)
let panX = 0
let panY = 0
let follow = true
// 用户是否已手动交互(交互后不再自动套用初始视图)
let userInteracted = false

let dragging = false
let lastX = 0
let lastY = 0

// 右键菜单(到达)状态
const NODE_HIT_RADIUS = 12 // 右键命中路径点的像素半径
const ctxMenu = ref<{ visible: boolean; x: number; y: number; name: string }>({
  visible: false,
  x: 0,
  y: 0,
  name: ''
})

// 地图离屏缓存(障碍点云预渲染到世界尺度的离屏画布，绘制时整体缩放 blit)
let mapCanvas: HTMLCanvasElement | null = null
let mapMeta: { minX: number; maxY: number; res: number; w: number; h: number } | null = null
interface Goal {
  name: string
  x: number
  y: number
}
interface PathEdge {
  a: Point
  b: Point
  bezier: boolean
  c1?: Point
  c2?: Point
}
let mapGoals: Goal[] = []
let mapPathPts: Record<string, Point> = {}
let mapPathEdges: PathEdge[] = []
let mapPathNodes: Array<{ name: string; x: number; y: number }> = []

function parsePoseStr(s?: string): Point {
  if (!s) return { x: 0, y: 0 }
  const p = s.trim().split(/\s+/).map((v) => parseFloat(v))
  return { x: p[0] || 0, y: p[1] || 0 }
}

function buildMapCache() {
  mapCanvas = null
  mapMeta = null
  mapGoals = []
  mapPathPts = {}
  mapPathEdges = []
  mapPathNodes = []
  const data = store.map.data
  if (!data) return

  const minP = String(data.MinPose || '0 0').trim().split(/\s+/).map((v: string) => parseFloat(v))
  const maxP = String(data.MaxPose || '0 0').trim().split(/\s+/).map((v: string) => parseFloat(v))
  const res = Math.max(1, Number(data.MapRes) || 20)
  const minX = minP[0]
  const minY = minP[1]
  const maxX = maxP[0]
  const maxY = maxP[1]
  const w = Math.max(1, Math.ceil((maxX - minX) / res))
  const h = Math.max(1, Math.ceil((maxY - minY) / res))

  if (Array.isArray(data.ObsPoints) && w * h <= 64_000_000) {
    const off = document.createElement('canvas')
    off.width = w
    off.height = h
    const octx = off.getContext('2d')
    if (octx) {
      octx.fillStyle = '#9aa7b8'
      // ObsPoints 每行为 [x, y1, y2, ...]
      for (const row of data.ObsPoints) {
        if (!Array.isArray(row) || row.length < 2) continue
        const x = row[0]
        const px = Math.round((x - minX) / res)
        for (let i = 1; i < row.length; i++) {
          const py = Math.round((maxY - row[i]) / res)
          if (px >= 0 && px < w && py >= 0 && py < h) octx.fillRect(px, py, 1, 1)
        }
      }
      mapCanvas = off
      mapMeta = { minX, maxY, res, w, h }
    }
  }

  // 目标点与路径点图
  const objs = data.Objs || {}
  if (Array.isArray(objs.GoalWithHeading)) {
    mapGoals = objs.GoalWithHeading.map((g: any) => {
      const p = parsePoseStr(g.pose)
      return { name: g.name || '', x: p.x, y: p.y }
    })
  }
  if (Array.isArray(objs.PathPoint)) {
    // 第一趟：收集节点
    for (const pp of objs.PathPoint) {
      if (pp && pp.name) {
        const pt = parsePoseStr(pp.pose)
        mapPathPts[pp.name] = pt
        mapPathNodes.push({ name: pp.name, x: pt.x, y: pt.y })
      }
    }
    // 第二趟：基于 connections 生成边(缺失时回退 vertex 画直线)
    for (const pp of objs.PathPoint) {
      if (!pp || !pp.name) continue
      const a = mapPathPts[pp.name]
      if (!a) continue
      if (Array.isArray(pp.connections) && pp.connections.length > 0) {
        for (const con of pp.connections) {
          if (!con || !con.name) continue
          const b = mapPathPts[con.name]
          if (!b) continue
          const type = Number(con.type) || 0
          if (type === 1) {
            const x1 = Number(con.x1) || 0
            const y1 = Number(con.y1) || 0
            const x2 = Number(con.x2) || 0
            const y2 = Number(con.y2) || 0
            if (x1 === 0 && y1 === 0 && x2 === 0 && y2 === 0) {
              mapPathEdges.push({ a, b, bezier: false })
            } else {
              mapPathEdges.push({
                a,
                b,
                bezier: true,
                c1: { x: a.x + x1, y: a.y + y1 },
                c2: { x: a.x + x2, y: a.y + y2 }
              })
            }
          } else {
            mapPathEdges.push({ a, b, bezier: false })
          }
        }
      } else if (pp.vertex) {
        const neighbors = String(pp.vertex).trim().split(/\s+/)
        for (const nb of neighbors) {
          const b = mapPathPts[nb]
          if (b) mapPathEdges.push({ a, b, bezier: false })
        }
      }
    }
  }

  // 地图首次就绪且用户尚未手动交互时，套用初始视图(整图自适应)
  if (!userInteracted) initialView()
}

watch(
  () => store.map.data,
  () => buildMapCache(),
  { deep: false }
)

function centerWorld(): Point {
  if (follow) {
    return { x: store.pose[0] + panX, y: store.pose[1] + panY }
  }
  return { x: panX, y: panY }
}

function worldToScreen(p: Point): Point {
  const c = cv.value!
  const cw = centerWorld()
  return {
    x: c.width / 2 + (p.x - cw.x) * scale.value,
    y: c.height / 2 - (p.y - cw.y) * scale.value
  }
}

// 以机器人当前位置为中心(跟随)，缩放保持不变
function recenter() {
  userInteracted = true
  follow = true
  panX = 0
  panY = 0
}

function zoom(factor: number) {
  userInteracted = true
  scale.value = Math.min(2, Math.max(0.001, scale.value * factor))
}

// 初始视图：以机器人为中心，固定初始分辨率(约 35px/m)，超出范围靠拖动查看
function initialView() {
  follow = true
  panX = 0
  panY = 0
  scale.value = INIT_SCALE
}

function onWheel(e: WheelEvent) {
  e.preventDefault()
  hideCtxMenu()
  zoom(e.deltaY < 0 ? 1.1 : 1 / 1.1)
}

function onDown(e: MouseEvent) {
  if (e.button === 2) return // 右键不触发拖动
  hideCtxMenu()
  const replayPoint = hitTestReplayPoint(e.offsetX, e.offsetY)
  if (replayPoint) {
    emit('select-replay-point', replayPoint)
    return
  }
  userInteracted = true
  dragging = true
  lastX = e.clientX
  lastY = e.clientY
}

function hitTestReplayPoint(px: number, py: number): ReplayPoint | null {
  const candidates = [...props.eventPoints, ...props.trajectory]
  let best = 10
  let hit: ReplayPoint | null = null
  for (const point of candidates) {
    const s = worldToScreen(point)
    const d = Math.hypot(s.x - px, s.y - py)
    if (d <= best) {
      best = d
      hit = point
    }
  }
  return hit
}

function onMove(e: MouseEvent) {
  if (!dragging) return
  const dx = e.clientX - lastX
  const dy = e.clientY - lastY
  lastX = e.clientX
  lastY = e.clientY
  if (follow) {
    // 拖动即脱离跟随，固定到当前中心
    const c = centerWorld()
    follow = false
    panX = c.x
    panY = c.y
  }
  panX -= dx / scale.value
  panY += dy / scale.value
}

function onUp() {
  dragging = false
}

// 命中检测：返回距(px,py)最近且在半径内的路径点名称，否则 null
function hitTestPathNode(px: number, py: number): string | null {
  let best = NODE_HIT_RADIUS
  let hit: string | null = null
  for (const n of mapPathNodes) {
    const s = worldToScreen(n)
    const d = Math.hypot(s.x - px, s.y - py)
    if (d <= best) {
      best = d
      hit = n.name
    }
  }
  return hit
}

function hideCtxMenu() {
  ctxMenu.value.visible = false
}

function onContextMenu(e: MouseEvent) {
  e.preventDefault()
  const name = hitTestPathNode(e.offsetX, e.offsetY)
  if (name) {
    ctxMenu.value = { visible: true, x: e.offsetX, y: e.offsetY, name }
  } else {
    ctxMenu.value.visible = false
  }
}

async function onAutoDrive() {
  const name = ctxMenu.value.name
  hideCtxMenu()
  if (!name) return
  try {
    const res = await control('autodrive', { goal_name: name })
    if (res && res.succeed) ElMessage.success('已下发到达: ' + name)
    else ElMessage.error('失败：' + (res && res.error ? res.error : '未知错误'))
  } catch (e: any) {
    ElMessage.error('请求失败：' + (e && e.message ? e.message : e))
  }
}

function drawGrid() {
  const c = cv.value!
  if (!ctx) return
  ctx.fillStyle = '#0b1220'
  ctx.fillRect(0, 0, c.width, c.height)
  ctx.strokeStyle = 'rgba(148,163,184,0.12)'
  ctx.lineWidth = 1
  const stepMm = 1000
  const stepPx = stepMm * scale.value
  if (stepPx < 6) return
  const cw = centerWorld()
  const startX = c.width / 2 - ((cw.x % stepMm) * scale.value)
  const startY = c.height / 2 + ((cw.y % stepMm) * scale.value)
  for (let x = startX % stepPx; x < c.width; x += stepPx) {
    ctx.beginPath()
    ctx.moveTo(x, 0)
    ctx.lineTo(x, c.height)
    ctx.stroke()
  }
  for (let y = startY % stepPx; y < c.height; y += stepPx) {
    ctx.beginPath()
    ctx.moveTo(0, y)
    ctx.lineTo(c.width, y)
    ctx.stroke()
  }
}

function drawMap() {
  if (!props.showMap || !ctx) return

  // 障碍点云(离屏整体缩放绘制)
  if (mapCanvas && mapMeta) {
    const topLeft = worldToScreen({ x: mapMeta.minX, y: mapMeta.maxY })
    const dw = mapMeta.w * mapMeta.res * scale.value
    const dh = mapMeta.h * mapMeta.res * scale.value
    ctx.imageSmoothingEnabled = false
    ctx.drawImage(mapCanvas, 0, 0, mapMeta.w, mapMeta.h, topLeft.x, topLeft.y, dw, dh)
  }

  // 路径点拓扑边(直线/贝塞尔)
  if (mapPathEdges.length > 0) {
    for (const e of mapPathEdges) {
      const sa = worldToScreen(e.a)
      const sb = worldToScreen(e.b)
      if (e.bezier && e.c1 && e.c2) {
        const sc1 = worldToScreen(e.c1)
        const sc2 = worldToScreen(e.c2)
        ctx.strokeStyle = PATH_BEZIER_COLOR
        ctx.lineWidth = 2
        ctx.beginPath()
        ctx.moveTo(sa.x, sa.y)
        ctx.bezierCurveTo(sc1.x, sc1.y, sc2.x, sc2.y, sb.x, sb.y)
        ctx.stroke()
      } else {
        ctx.strokeStyle = PATH_STRAIGHT_COLOR
        ctx.lineWidth = 1
        ctx.beginPath()
        ctx.moveTo(sa.x, sa.y)
        ctx.lineTo(sb.x, sb.y)
        ctx.stroke()
      }
    }
  }

  // 路径点节点方块 + 名称
  if (mapPathNodes.length > 0) {
    const half = PATH_NODE_SIZE / 2
    ctx.fillStyle = PATH_NODE_COLOR
    for (const n of mapPathNodes) {
      const s = worldToScreen(n)
      ctx.fillRect(s.x - half, s.y - half, PATH_NODE_SIZE, PATH_NODE_SIZE)
    }
    if (scale.value >= PATH_LABEL_MIN_SCALE) {
      ctx.fillStyle = PATH_NODE_TEXT_COLOR
      ctx.font = '11px sans-serif'
      for (const n of mapPathNodes) {
        const s = worldToScreen(n)
        ctx.fillText(n.name, s.x + 6, s.y - 6)
      }
    }
  }

  // 目标点
  if (mapGoals.length > 0) {
    ctx.fillStyle = '#a78bfa'
    ctx.font = '11px sans-serif'
    for (const g of mapGoals) {
      const s = worldToScreen(g)
      ctx.beginPath()
      ctx.arc(s.x, s.y, 4, 0, Math.PI * 2)
      ctx.fill()
      ctx.fillText(g.name, s.x + 6, s.y - 6)
    }
  }
}

function drawPath() {
  if (!ctx || store.pathPoints.length === 0) return
  ctx.strokeStyle = '#38bdf8'
  ctx.lineWidth = 2
  ctx.beginPath()
  store.pathPoints.forEach((p, i) => {
    const s = worldToScreen(p)
    if (i === 0) ctx!.moveTo(s.x, s.y)
    else ctx!.lineTo(s.x, s.y)
  })
  ctx.stroke()
}

function drawReplayOverlays() {
  if (!ctx) return
  if (props.trajectory.length > 1) {
    ctx.strokeStyle = 'rgba(34,197,94,0.8)'
    ctx.lineWidth = 2
    ctx.beginPath()
    props.trajectory.forEach((p, i) => {
      const s = worldToScreen(p)
      if (i === 0) ctx!.moveTo(s.x, s.y)
      else ctx!.lineTo(s.x, s.y)
    })
    ctx.stroke()
  }
  if (props.eventPoints.length > 0) {
    ctx.fillStyle = '#ef4444'
    ctx.strokeStyle = '#fff'
    ctx.lineWidth = 1
    for (const p of props.eventPoints) {
      const s = worldToScreen(p)
      ctx.beginPath()
      ctx.arc(s.x, s.y, 4, 0, Math.PI * 2)
      ctx.fill()
      ctx.stroke()
    }
  }
}

function drawClearances() {
  if (!ctx || store.clearances.length < 2) return
  ctx.strokeStyle = '#f59e0b'
  ctx.lineWidth = 2
  // 将 clearances 各点按顺序连成一圈闭合多边形
  ctx.beginPath()
  store.clearances.forEach((p, i) => {
    const s = worldToScreen(p)
    if (i === 0) ctx!.moveTo(s.x, s.y)
    else ctx!.lineTo(s.x, s.y)
  })
  ctx.closePath()
  ctx.stroke()
}

// 基于避障参数(已保存值)绘制配置范围框：车体边缘外扩 min 距离，一圈虚线
function drawAvoidBox() {
  if (!ctx || !props.showAvoidBox) return
  const av = store.paramsInfo['avoid']
  if (!av) return
  const fmin = Number(av.clearance_front_min?.default)
  const bmin = Number(av.clearance_back_min?.default)
  const smin = Number(av.clearance_side_min?.default)
  if (!isFinite(fmin) || !isFinite(bmin) || !isFinite(smin)) return

  const [x, y, thDeg] = store.pose
  const th = (thDeg * Math.PI) / 180
  const size = store.robotSize
  const front = (size.length_front || size.length / 2 || 400) + fmin
  const rear = (size.length_rear || size.length / 2 || 400) + bmin
  const halfW = (size.width || 600) / 2 + smin

  const corners: Point[] = [
    { x: front, y: halfW },
    { x: front, y: -halfW },
    { x: -rear, y: -halfW },
    { x: -rear, y: halfW }
  ]
  const screenPts = corners.map((c) => {
    const wx = x + c.x * Math.cos(th) - c.y * Math.sin(th)
    const wy = y + c.x * Math.sin(th) + c.y * Math.cos(th)
    return worldToScreen({ x: wx, y: wy })
  })

  ctx.setLineDash([6, 4])
  ctx.strokeStyle = '#e5e7eb'
  ctx.lineWidth = 2
  ctx.beginPath()
  screenPts.forEach((p, i) => {
    if (i === 0) ctx!.moveTo(p.x, p.y)
    else ctx!.lineTo(p.x, p.y)
  })
  ctx.closePath()
  ctx.stroke()
  ctx.setLineDash([])
}

function drawLaser() {
  if (!ctx) return
  store.laserGroups.forEach((group, i) => {
    ctx!.fillStyle = LASER_COLORS[i % LASER_COLORS.length]
    for (const p of group) {
      const s = worldToScreen(p)
      ctx!.fillRect(s.x - 1, s.y - 1, 2, 2)
    }
  })
}

function drawRobot() {
  if (!ctx) return
  const [x, y, thDeg] = store.pose
  const th = (thDeg * Math.PI) / 180
  const size = store.robotSize
  const front = size.length_front || size.length / 2 || 400
  const rear = size.length_rear || size.length / 2 || 400
  const halfW = (size.width || 600) / 2

  const corners: Point[] = [
    { x: front, y: halfW },
    { x: front, y: -halfW },
    { x: -rear, y: -halfW },
    { x: -rear, y: halfW }
  ]
  const screenPts = corners.map((c) => {
    const wx = x + c.x * Math.cos(th) - c.y * Math.sin(th)
    const wy = y + c.x * Math.sin(th) + c.y * Math.cos(th)
    return worldToScreen({ x: wx, y: wy })
  })

  ctx.strokeStyle = '#22c55e'
  ctx.lineWidth = 2
  ctx.beginPath()
  screenPts.forEach((p, i) => {
    if (i === 0) ctx!.moveTo(p.x, p.y)
    else ctx!.lineTo(p.x, p.y)
  })
  ctx.closePath()
  ctx.stroke()

  // 朝向线
  const center = worldToScreen({ x, y })
  const head = worldToScreen({ x: x + front * Math.cos(th), y: y + front * Math.sin(th) })
  ctx.strokeStyle = '#86efac'
  ctx.beginPath()
  ctx.moveTo(center.x, center.y)
  ctx.lineTo(head.x, head.y)
  ctx.stroke()

  ctx.fillStyle = '#22c55e'
  ctx.beginPath()
  ctx.arc(center.x, center.y, 3, 0, Math.PI * 2)
  ctx.fill()
}

function render() {
  if (ctx && cv.value) {
    drawGrid()
    drawMap()
    drawReplayOverlays()
    drawPath()
    drawClearances()
    drawAvoidBox()
    drawLaser()
    drawRobot()
  }
  raf = requestAnimationFrame(render)
}

function resize() {
  const w = wrap.value
  const c = cv.value
  if (!w || !c) return
  c.width = w.clientWidth
  c.height = w.clientHeight
}

onMounted(() => {
  ctx = cv.value!.getContext('2d')
  resize()
  ro = new ResizeObserver(resize)
  if (wrap.value) ro.observe(wrap.value)
  const c = cv.value!
  c.addEventListener('wheel', onWheel, { passive: false })
  c.addEventListener('mousedown', onDown)
  c.addEventListener('contextmenu', onContextMenu)
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
  buildMapCache()
  if (!userInteracted) initialView()
  render()
})

onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  if (ro && wrap.value) ro.unobserve(wrap.value)
  const c = cv.value
  if (c) {
    c.removeEventListener('wheel', onWheel)
    c.removeEventListener('mousedown', onDown)
    c.removeEventListener('contextmenu', onContextMenu)
  }
  window.removeEventListener('mousemove', onMove)
  window.removeEventListener('mouseup', onUp)
})
</script>

<style scoped>
.canvas-wrap {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 240px;
  background: #0b1220;
  border-radius: 8px;
  overflow: hidden;
}
.canvas-el {
  display: block;
  width: 100%;
  height: 100%;
  cursor: grab;
}
.canvas-el:active {
  cursor: grabbing;
}
.canvas-toolbar {
  position: absolute;
  top: 10px;
  right: 10px;
  display: flex;
  gap: 6px;
  align-items: center;
}
.canvas-legend {
  position: absolute;
  left: 10px;
  bottom: 10px;
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 6px 8px;
  background: rgba(11, 18, 32, 0.7);
  border-radius: 6px;
}
.legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #cbd5e1;
  font-size: 12px;
  line-height: 1;
}
.legend-swatch {
  width: 10px;
  height: 10px;
  border-radius: 2px;
  display: inline-block;
}
.ctx-menu {
  position: absolute;
  z-index: 20;
  background: rgba(11, 18, 32, 0.95);
  border: 1px solid #334155;
  border-radius: 6px;
  padding: 4px;
  min-width: 96px;
}
.ctx-menu-item {
  padding: 6px 10px;
  color: #e5e7eb;
  font-size: 13px;
  cursor: pointer;
  border-radius: 4px;
}
.ctx-menu-item:hover {
  background: #1e293b;
}
</style>
