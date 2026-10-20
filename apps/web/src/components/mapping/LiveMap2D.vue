<template>
  <canvas ref="canvas" class="map"></canvas>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps<{
  png: ImageBitmap | null
  meta: { resolution: number; originX: number; originY: number; width: number; height: number } | null
  trajectory: number[][]
  pose: { x: number; y: number; yaw: number } | null
  scan: number[][]
}>()
const canvas = ref<HTMLCanvasElement | null>(null)
const view = { cx: 0, cy: 0, scale: 40 }
let dragging = false
let lastX = 0
let lastY = 0

function draw() {
  const el = canvas.value
  if (!el) return
  const ctx = el.getContext('2d')
  if (!ctx) return
  const width = el.clientWidth
  const height = el.clientHeight
  if (el.width !== width || el.height !== height) {
    el.width = width
    el.height = height
  }
  ctx.clearRect(0, 0, width, height)
  ctx.fillStyle = getComputedStyle(el).getPropertyValue('--hud-bg') || '#0b0f0c'
  ctx.fillRect(0, 0, width, height)
  const toScreen = (x: number, y: number) => [width / 2 + (x - view.cx) * view.scale, height / 2 - (y - view.cy) * view.scale]
  if (props.png && props.meta) {
    const { originX, originY, resolution, width: cols, height: rows } = props.meta
    const [sx, sy] = toScreen(originX, originY + rows * resolution)
    ctx.imageSmoothingEnabled = false
    ctx.drawImage(props.png, sx, sy, cols * resolution * view.scale, rows * resolution * view.scale)
  }
  ctx.strokeStyle = '#b6ff3b'
  ctx.beginPath()
  props.trajectory.forEach((point, index) => {
    const [sx, sy] = toScreen(point[0], point[1])
    if (index === 0) ctx.moveTo(sx, sy)
    else ctx.lineTo(sx, sy)
  })
  ctx.stroke()
  ctx.fillStyle = '#7fd1ff'
  for (const point of props.scan) {
    const [sx, sy] = toScreen(point[0], point[1])
    ctx.fillRect(sx, sy, 2, 2)
  }
  if (props.pose) {
    const [sx, sy] = toScreen(props.pose.x, props.pose.y)
    ctx.save()
    ctx.translate(sx, sy)
    ctx.rotate(-props.pose.yaw)
    ctx.fillStyle = '#b6ff3b'
    ctx.beginPath()
    ctx.moveTo(12, 0)
    ctx.lineTo(-8, 6)
    ctx.lineTo(-8, -6)
    ctx.closePath()
    ctx.fill()
    ctx.restore()
  }
}
function fit() {
  if (!props.meta) return
  const el = canvas.value
  if (!el) return
  const worldW = props.meta.width * props.meta.resolution
  const worldH = props.meta.height * props.meta.resolution
  view.cx = props.meta.originX + worldW / 2
  view.cy = props.meta.originY + worldH / 2
  view.scale = Math.max(4, Math.min(el.clientWidth / worldW, el.clientHeight / worldH) * 0.9)
  draw()
}
watch(() => [props.png, props.trajectory, props.pose, props.scan], draw)
onMounted(() => {
  const el = canvas.value!
  const observer = new ResizeObserver(() => {
    const before = { ...view }
    draw()
    view.cx = before.cx
    view.cy = before.cy
    view.scale = before.scale
    draw()
  })
  observer.observe(el)
  el.addEventListener('pointerdown', (ev) => {
    dragging = true
    lastX = ev.clientX
    lastY = ev.clientY
  })
  window.addEventListener('pointerup', () => { dragging = false })
  el.addEventListener('pointermove', (ev) => {
    if (!dragging) return
    view.cx -= (ev.clientX - lastX) / view.scale
    view.cy += (ev.clientY - lastY) / view.scale
    lastX = ev.clientX
    lastY = ev.clientY
    draw()
  })
  el.addEventListener('wheel', (ev) => {
    ev.preventDefault()
    view.scale = Math.max(2, Math.min(400, view.scale * (ev.deltaY > 0 ? 0.9 : 1.1)))
    draw()
  }, { passive: false })
  ;(el as any)._fit = fit
  ;(el as any)._observer = observer
})
onBeforeUnmount(() => canvas.value && (canvas.value as any)._observer?.disconnect())
defineExpose({ fit })
</script>

<style scoped>
.map { width: 100%; height: 100%; display: block; background: var(--hud-bg, #0b0f0c); touch-action: none; }
</style>
