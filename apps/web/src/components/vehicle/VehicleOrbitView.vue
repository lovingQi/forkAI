<template>
  <div ref="hostEl" class="orbit" data-testid="vehicle-orbit-view">
    <p v-if="note" class="note">{{ note }}</p>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { disposeVehicle, loadVehicleModel, type VehicleModel } from '@/vehicle/model'

const TURN_MS = 18000
const REST_AZIMUTH = 0.8

const hostEl = ref<HTMLElement | null>(null)
const note = ref('')
const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches

let renderer: THREE.WebGLRenderer | null = null
let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | null = null
let controls: OrbitControls | null = null
let observer: ResizeObserver | null = null
let vehicle: VehicleModel | null = null
let frame = 0
let stopped = false
let spinning = !reduced
let azimuth = reduced ? REST_AZIMUTH : 0
let last = 0
const target = new THREE.Vector3()
let sphereRadius = 1

function fitDistance() {
  if (!camera) return 4
  const verticalHalf = THREE.MathUtils.degToRad(camera.fov) / 2
  const horizontalHalf = Math.atan(Math.tan(verticalHalf) * Math.max(camera.aspect, 0.01))
  const half = Math.min(verticalHalf, horizontalHalf)
  return sphereRadius / Math.sin(half) * 1.08
}

function placeCamera(angle: number) {
  if (!camera || !controls) return
  const distance = fitDistance()
  const lift = distance * 0.28
  const horizontal = Math.sqrt(Math.max(distance * distance - lift * lift, 0.01))
  camera.position.set(
    target.x + Math.sin(angle) * horizontal,
    target.y + lift,
    target.z + Math.cos(angle) * horizontal
  )
  camera.lookAt(target)
  controls.target.copy(target)
}

function render() {
  if (renderer && scene && camera) renderer.render(scene, camera)
}

function tick(now: number) {
  frame = requestAnimationFrame(tick)
  const delta = Math.min(last ? now - last : 0, 32)
  last = now
  if (!spinning || !camera || !controls) return
  azimuth += (delta / TURN_MS) * Math.PI * 2
  placeCamera(azimuth)
  render()
}

function resize() {
  const host = hostEl.value
  if (!host || !renderer || !camera) return
  const width = host.clientWidth || 640
  const height = host.clientHeight || 360
  camera.aspect = width / Math.max(height, 1)
  camera.updateProjectionMatrix()
  renderer.setSize(width, height, false)
  placeCamera(azimuth)
  render()
}

function holdStill() {
  spinning = false
}

function resumeSpin() {
  if (reduced || !camera) return
  azimuth = Math.atan2(camera.position.x - target.x, camera.position.z - target.z)
  spinning = true
  last = performance.now()
}

async function boot() {
  const host = hostEl.value
  if (!host) return
  scene = new THREE.Scene()
  scene.background = new THREE.Color(0x0c120e)
  camera = new THREE.PerspectiveCamera(40, 1, 0.05, 200)
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true })
  } catch {
    note.value = '当前环境没有 WebGL'
    return
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.outputColorSpace = THREE.SRGBColorSpace
  host.appendChild(renderer.domElement)
  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = false
  controls.addEventListener('change', () => {
    if (!spinning) render()
  })
  controls.addEventListener('end', resumeSpin)
  renderer.domElement.addEventListener('pointerdown', holdStill)
  scene.add(new THREE.AmbientLight(0xffffff, 0.7))
  const sun = new THREE.DirectionalLight(0xffffff, 1.3)
  sun.position.set(4, 8, 6)
  scene.add(sun)
  observer = new ResizeObserver(resize)
  observer.observe(host)
  resize()
  try {
    const model = await loadVehicleModel()
    if (stopped) {
      disposeVehicle(model.root)
      return
    }
    vehicle = model
    const center = model.bounds.getCenter(new THREE.Vector3())
    const size = model.bounds.getSize(new THREE.Vector3())
    model.root.position.set(-center.x, -model.bounds.min.y, -center.z)
    scene.add(model.root)
    target.set(0, size.y / 2, 0)
    sphereRadius = 0.5 * Math.hypot(size.x, size.y, size.z)
    placeCamera(azimuth)
    controls.update()
    render()
    if (!reduced) {
      last = performance.now()
      frame = requestAnimationFrame(tick)
    }
  } catch {
    if (!stopped) note.value = '车辆模型加载失败'
  }
}

onMounted(() => {
  void boot()
})

onBeforeUnmount(() => {
  stopped = true
  spinning = false
  cancelAnimationFrame(frame)
  observer?.disconnect()
  renderer?.domElement.removeEventListener('pointerdown', holdStill)
  controls?.dispose()
  if (vehicle) disposeVehicle(vehicle.root)
  vehicle = null
  renderer?.dispose()
})
</script>

<style scoped>
.orbit {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: #0c120e;
}
.orbit :deep(canvas) {
  display: block;
  width: 100%;
  height: 100%;
}
.note {
  position: absolute;
  left: 12px;
  top: 12px;
  margin: 0;
  color: #fbbf24;
}
</style>
