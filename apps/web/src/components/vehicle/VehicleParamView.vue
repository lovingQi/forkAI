<template>
  <div ref="hostEl" class="vehicle-view" data-testid="vehicle-param-view" :data-draft="drafting ? '1' : '0'">
    <div class="tools">
      <el-button size="small" @click="showTop">俯视</el-button>
      <el-button size="small" @click="showPerspective">三维</el-button>
    </div>
    <p v-if="note" class="note">{{ note }}</p>
    <div class="hints">
      <p>按已保存参数示意，实际保护范围以车端为准</p>
      <p v-if="mismatch">参数车体 {{ mismatch.param }} mm，模型 {{ mismatch.model }} mm，避障框按参数绘制</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import type { ParamSection } from '@/api/console'
import { disposeVehicle, loadVehicleModel, robotCenterZ, type VehicleModel } from '@/vehicle/model'
import {
  buildAvoidBoxes,
  buildLaserFan,
  laserScale,
  type AvoidInput,
  type LaserInput
} from '@/vehicle/overlays'

const LASERS = ['laser', 'laser_back', 'laser_left', 'laser_right', 'laser_top'] as const
const LASER_COLOR: Record<string, number> = {
  laser: 0x38bdf8,
  laser_back: 0xf472b6,
  laser_left: 0xc084fc,
  laser_right: 0xa3e635,
  laser_top: 0xfbbf24
}

const props = defineProps<{
  sections: Record<string, ParamSection>
  drafts?: Record<string, Record<string, unknown>>
  focus?: { section: string; key: string } | null
}>()

const hostEl = ref<HTMLElement | null>(null)
const note = ref('')
const mismatch = ref<{ param: string; model: string } | null>(null)
const drafting = computed(() => Object.values(props.drafts || {}).some((section) => Object.keys(section).length > 0))

let renderer: THREE.WebGLRenderer | null = null
let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | null = null
let controls: OrbitControls | null = null
let observer: ResizeObserver | null = null
let vehicle: VehicleModel | null = null
let content: THREE.Group | null = null
let stopped = false
let frame = 0

function read(section: string, key: string, source: 'saved' | 'draft'): number {
  const saved = Number(props.sections[section]?.items[key]?.default)
  if (source === 'saved') return saved
  const draft = props.drafts?.[section]
  if (draft && draft[key] !== undefined && draft[key] !== '') return Number(draft[key])
  return saved
}

function flag(section: string, key: string, source: 'saved' | 'draft'): boolean {
  const saved = !!props.sections[section]?.items[key]?.default
  if (source === 'saved') return saved
  const draft = props.drafts?.[section]
  if (draft && draft[key] !== undefined) return !!draft[key]
  return saved
}

function avoidInput(source: 'saved' | 'draft'): AvoidInput {
  return {
    widthMm: read('robot', 'size_width', source),
    lengthFrontMm: read('robot', 'size_length_front', source),
    lengthRearMm: read('robot', 'size_length_rear', source),
    frontMin: read('avoid', 'clearance_front_min', source),
    frontMax: read('avoid', 'clearance_front_max', source),
    backMin: read('avoid', 'clearance_back_min', source),
    backMax: read('avoid', 'clearance_back_max', source),
    sideMin: read('avoid', 'clearance_side_min', source),
    sideMax: read('avoid', 'clearance_side_max', source),
    slow: read('avoid', 'clearance_slow_vel', source),
    fast: read('avoid', 'clearance_fast_vel', source)
  }
}

function laserEnabled(name: string, source: 'saved' | 'draft'): boolean {
  if (!props.sections[name]) return false
  if (!props.sections.robot) return true
  if (name === 'laser') return true
  if (name === 'laser_back') return flag('robot', 'has_backLaser', source)
  if (name === 'laser_left' || name === 'laser_right') return flag('robot', 'has_sideLaser', source)
  if (name === 'laser_top') return flag('robot', 'has_topLaser', source)
  return false
}

function laserInput(name: string, source: 'saved' | 'draft'): LaserInput {
  return {
    posX: read(name, 'pos_x', source),
    posY: read(name, 'pos_y', source),
    posTh: read(name, 'pos_th', source),
    angleMin: read(name, 'angle_min', source),
    angleMax: read(name, 'angle_max', source),
    ignore: String(source === 'draft' && props.drafts?.[name]?.angle_ignore !== undefined
      ? props.drafts[name].angle_ignore
      : props.sections[name]?.items.angle_ignore?.default || ''),
    rangeMin: read(name, 'range_min', source),
    rangeMax: read(name, 'range_max', source),
    flipped: flag(name, 'flipped', source)
  }
}

function sectionDirty(name: string): boolean {
  return Object.keys(props.drafts?.[name] || {}).length > 0
}

function disposeObject(object: THREE.Object3D) {
  object.traverse((child) => {
    const mesh = child as THREE.Mesh
    const texture = child.userData.labelTexture as THREE.Texture | undefined
    texture?.dispose()
    if (!mesh.geometry && !(child as THREE.Sprite).material) return
    mesh.geometry?.dispose()
    const material = (child as THREE.Mesh | THREE.Sprite | THREE.Line).material
    const materials = Array.isArray(material) ? material : material ? [material] : []
    materials.forEach((item) => item.dispose())
  })
}

function dimFor(section: string, role: string): number {
  const focus = props.focus
  if (!focus || !focus.section) return 1
  if (focus.section !== section) return 0.25
  if (!focus.key) return 1
  if (section === 'avoid') {
    if (focus.key.endsWith('_min')) return role === 'inner' ? 1 : 0.25
    if (focus.key.endsWith('_max')) return role === 'outer' ? 1 : 0.25
    if (focus.key === 'clearance_fast_vel' || focus.key === 'clearance_slow_vel') return role === 'speed' ? 1 : 0.25
    return 1
  }
  if (section === 'robot') return focus.key.startsWith('size_') ? (role === 'body' ? 1 : 0.25) : 1
  return 1
}

function applyFocus(root: THREE.Object3D) {
  root.traverse((child) => {
    let section = ''
    let role = ''
    let cursor: THREE.Object3D | null = child
    while (cursor) {
      if (!section && cursor.userData.section) section = String(cursor.userData.section)
      if (!role && cursor.userData.role) role = String(cursor.userData.role)
      cursor = cursor.parent
    }
    if (!section) return
    const opacity = dimFor(section, role || 'all')
    const material = (child as THREE.Mesh).material
    const materials = Array.isArray(material) ? material : material ? [material] : []
    materials.forEach((item) => {
      item.transparent = true
      item.opacity = opacity * (item.userData.baseOpacity ?? (item.opacity || 1))
    })
  })
}

function rememberOpacity(root: THREE.Object3D) {
  root.traverse((child) => {
    const material = (child as THREE.Mesh).material
    const materials = Array.isArray(material) ? material : material ? [material] : []
    materials.forEach((item) => {
      if (item.userData.baseOpacity === undefined) item.userData.baseOpacity = item.opacity
    })
  })
}

function drawOverlays() {
  if (!content) return
  const previous = content.getObjectByName('overlays')
  if (previous) {
    content.remove(previous)
    disposeObject(previous)
  }
  const overlays = new THREE.Group()
  overlays.name = 'overlays'
  const ranges: number[] = []
  for (const name of LASERS) {
    for (const source of ['saved', 'draft'] as const) {
      if (!laserEnabled(name, source)) continue
      ranges.push(laserInput(name, source).rangeMax)
    }
  }
  const scale = laserScale(ranges)
  overlays.add(buildAvoidBoxes(avoidInput('saved'), false))
  if (sectionDirty('avoid') || sectionDirty('robot')) overlays.add(buildAvoidBoxes(avoidInput('draft'), true))
  for (const name of LASERS) {
    if (laserEnabled(name, 'saved')) {
      const fan = buildLaserFan(laserInput(name, 'saved'), scale, false, LASER_COLOR[name])
      fan.userData.section = name
      fan.userData.role = 'laser'
      overlays.add(fan)
    }
    if (sectionDirty(name) && laserEnabled(name, 'draft')) {
      const fan = buildLaserFan(laserInput(name, 'draft'), scale, true, LASER_COLOR[name])
      fan.userData.section = name
      fan.userData.role = 'laser'
      overlays.add(fan)
    }
  }
  rememberOpacity(overlays)
  applyFocus(overlays)
  content.add(overlays)
  updateMismatch()
}

function updateMismatch() {
  if (!vehicle) {
    mismatch.value = null
    return
  }
  const width = read('robot', 'size_width', 'saved')
  const front = read('robot', 'size_length_front', 'saved')
  const rear = read('robot', 'size_length_rear', 'saved')
  const modelW = Math.round((vehicle.bounds.max.x - vehicle.bounds.min.x) * 1000)
  const modelL = Math.round((vehicle.bounds.max.z - vehicle.bounds.min.z) * 1000)
  if (![width, front, rear].every(Number.isFinite) || Math.abs(modelW - width) <= 100 && Math.abs(modelL - (front + rear)) <= 100) {
    mismatch.value = null
    return
  }
  mismatch.value = { param: `${Math.round(width)}×${Math.round(front + rear)}`, model: `${modelW}×${modelL}` }
}

function frameOf(object: THREE.Object3D) {
  const box = new THREE.Box3().setFromObject(object)
  if (box.isEmpty()) box.set(new THREE.Vector3(-1, 0, -1), new THREE.Vector3(1, 1, 1))
  return { box, center: box.getCenter(new THREE.Vector3()), size: box.getSize(new THREE.Vector3()) }
}

function showTop() {
  if (!camera || !controls || !content) return
  const { center, size } = frameOf(content)
  const span = Math.max(size.x, size.z, 1)
  camera.up.set(0, 0, 1)
  camera.position.set(center.x, center.y + span * 0.9, center.z)
  controls.target.copy(center)
  controls.update()
  render()
}

function showPerspective() {
  if (!camera || !controls || !content) return
  const { center, size } = frameOf(content)
  camera.up.set(0, 1, 0)
  camera.position.set(center.x + size.x * 0.8, center.y + Math.max(size.y, 1), center.z + size.z)
  controls.target.copy(center)
  controls.update()
  render()
}

function render() {
  if (renderer && scene && camera) renderer.render(scene, camera)
}

function resize() {
  const host = hostEl.value
  if (!host || !renderer || !camera) return
  const width = host.clientWidth || 640
  const height = host.clientHeight || 360
  camera.aspect = width / Math.max(height, 1)
  camera.updateProjectionMatrix()
  renderer.setSize(width, height)
  render()
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
  controls.addEventListener('change', render)
  scene.add(new THREE.AmbientLight(0xffffff, 0.7))
  const sun = new THREE.DirectionalLight(0xffffff, 1.3)
  sun.position.set(4, 8, 6)
  scene.add(sun)
  content = new THREE.Group()
  scene.add(content)
  drawOverlays()
  resize()
  observer = new ResizeObserver(resize)
  observer.observe(host)
  showTop()
  try {
    const model = await loadVehicleModel()
    if (stopped) {
      disposeVehicle(model.root)
      return
    }
    vehicle = model
    const front = read('robot', 'size_length_front', 'saved')
    model.root.position.set(0, -model.bounds.min.y, -robotCenterZ(model, front))
    model.root.traverse((child) => {
      const mesh = child as THREE.Mesh
      if (mesh.isMesh) mesh.castShadow = false
    })
    content.add(model.root)
    note.value = ''
    updateMismatch()
    showTop()
  } catch {
    if (!stopped) note.value = '车辆模型加载失败'
  }
}

watch(() => [props.sections, props.drafts, props.focus], () => {
  drawOverlays()
  render()
}, { deep: true })

onMounted(() => {
  frame = requestAnimationFrame(() => { void boot() })
})
onBeforeUnmount(() => {
  stopped = true
  cancelAnimationFrame(frame)
  observer?.disconnect()
  controls?.dispose()
  if (content) disposeObject(content)
  vehicle = null
  renderer?.dispose()
})
</script>

<style scoped>
.vehicle-view { position: relative; width: 100%; height: 100%; min-height: 360px; background: #0c120e; }
.tools { position: absolute; top: 8px; right: 8px; z-index: 2; display: flex; gap: 8px; }
.note { position: absolute; left: 12px; top: 12px; z-index: 2; margin: 0; color: #fbbf24; }
.hints { position: absolute; left: 12px; bottom: 8px; z-index: 2; margin: 0; color: var(--hud-muted, #7f8f84); font-size: 12px; }
.hints p { margin: 0; }
</style>
