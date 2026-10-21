<template>
  <div ref="hostEl" class="intro" data-testid="boot-intro" aria-hidden="true">
    <canvas ref="canvasEl"></canvas>
    <div class="veil" :style="{ opacity: String(veil) }"></div>
    <div v-show="bezelVisible" ref="bezelEl" class="bezel" :style="bezelStyle">
      <div class="bezel-hole" :style="{ height: holeHeight, margin: holeMargin }"></div>
      <div class="bezel-name" :style="{ height: nameHeight }">
        <BrandLockup />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import * as THREE from 'three'
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js'
import diffUrl from '@/assets/boot/plywood_diff.jpg'
import norUrl from '@/assets/boot/plywood_nor.jpg'
import roughUrl from '@/assets/boot/plywood_rough.jpg'
import BrandLockup from './BrandLockup.vue'
import { lockupSvg } from './lockup'

const emit = defineEmits<{ done: []; fallback: [] }>()

const hostEl = ref<HTMLElement | null>(null)
const canvasEl = ref<HTMLCanvasElement | null>(null)
const bezelEl = ref<HTMLElement | null>(null)
const veil = ref(1)
const bezelVisible = ref(false)
const holeHeight = ref('0px')
const holeMargin = ref('0px')
const nameHeight = ref('0px')
const bezelStyle = reactive({
  left: '0px',
  top: '0px',
  width: '0px',
  height: '0px',
  opacity: '1'
})

const INTRO_MS = 8000
const CRATE_W = 1.5
const CRATE_H = 1.6
const CRATE_D = 1.5
const FLOOR_Y = 0.28
const PANEL_LEN = 1.6
const DOOR_ANGLE = Math.PI / 2 + Math.asin(FLOOR_Y / PANEL_LEN)
const TRUCK_SCALE = 0.55
const SCREEN_W = 0.36
const SCREEN_H = 0.22
const FLOOR_LEN = 0.85
const RAMP_LEN = PANEL_LEN
const GROUND_LEN = 1.275
const STOP_Z = 3.6

let stopped = false
let finished = false
let raf = 0
let elapsed = 0
let lastTick = 0
let wasHidden = false
let renderer: THREE.WebGLRenderer | null = null
let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | null = null
let truck: THREE.Group | null = null
let screen: THREE.Mesh | null = null
let doorPivot: THREE.Group | null = null
let logoTexture: THREE.CanvasTexture | null = null
let logoUrl = ''
let resizeObserver: ResizeObserver | null = null
let card: HTMLElement | null = null
let natural = { left: 0, top: 0, width: 560, height: 180 }
let pmrem: THREE.PMREMGenerator | null = null
let envTexture: THREE.Texture | null = null
let room: THREE.Scene | null = null
let gradientTexture: THREE.CanvasTexture | null = null
let woodTextures: THREE.Texture[] = []
let camCurve: THREE.CatmullRomCurve3 | null = null
let lookCurve: THREE.CatmullRomCurve3 | null = null
let rampPitch = Math.asin(FLOOR_Y / PANEL_LEN)

const inside = new THREE.Vector3(0, FLOOR_Y, -0.1)
const hinge = new THREE.Vector3(0, FLOOR_Y, CRATE_D / 2)
const rampTip = new THREE.Vector3()
const rampScratch = new THREE.Vector3()
const pose = new THREE.Vector3()
const axisX = new THREE.Vector3(1, 0, 0)
const up = new THREE.Vector3(0, 1, 0)
const qYaw = new THREE.Quaternion()
const qPitch = new THREE.Quaternion()
const normal = new THREE.Vector3()
const screenCenter = new THREE.Vector3()
const projected = new THREE.Vector3()
const camKeys = [
  new THREE.Vector3(3.3, 2.0, 5.6),
  new THREE.Vector3(2.2, 1.6, 6.6),
  new THREE.Vector3(0, 0.85, 6.4),
  new THREE.Vector3()
]
const lookKeys = [
  new THREE.Vector3(0.35, 0.8, 0.9),
  new THREE.Vector3(0.2, 0.6, 1.8),
  new THREE.Vector3(),
  new THREE.Vector3()
]
const camTimes = [0, 2960, 5560, 7600]

function clamp01(value: number) {
  return Math.min(1, Math.max(0, value))
}
function linear(now: number, start: number, end: number) {
  return clamp01((now - start) / (end - start))
}
function smooth(now: number, start: number, end: number) {
  const t = linear(now, start, end)
  return t * t * (3 - 2 * t)
}

function rampPoint(distance: number, out: THREE.Vector3) {
  rampScratch.set(0, distance, 0).applyAxisAngle(axisX, DOOR_ANGLE)
  out.copy(hinge).add(rampScratch)
}

function distanceAt(now: number) {
  if (now <= 1900) return 0
  if (now < 2960) {
    const t = (now - 1900) / 1060
    return FLOOR_LEN * t * t
  }
  if (now < 3960) return FLOOR_LEN + RAMP_LEN * ((now - 2960) / 1000)
  if (now < 5560) {
    const u = (now - 3960) / 1600
    return FLOOR_LEN + RAMP_LEN + GROUND_LEN * (2 * u - u * u)
  }
  return FLOOR_LEN + RAMP_LEN + GROUND_LEN
}

function face(pitch: number) {
  if (!truck) return
  qYaw.setFromAxisAngle(up, -Math.PI / 2)
  qPitch.setFromAxisAngle(axisX, pitch)
  truck.quaternion.copy(qYaw).multiply(qPitch)
}

function addBox(
  parent: THREE.Object3D,
  w: number,
  h: number,
  d: number,
  x: number,
  y: number,
  z: number,
  material: THREE.Material
) {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material)
  mesh.position.set(x, y, z)
  mesh.castShadow = true
  mesh.receiveShadow = true
  parent.add(mesh)
  return mesh
}

function loadTexture(url: string, colorSpace: THREE.ColorSpace) {
  return new Promise<THREE.Texture>((resolve, reject) => {
    new THREE.TextureLoader().load(url, (texture) => {
      texture.colorSpace = colorSpace
      texture.wrapS = THREE.RepeatWrapping
      texture.wrapT = THREE.RepeatWrapping
      texture.repeat.set(3, 3.2)
      resolve(texture)
    }, undefined, () => reject(new Error(url)))
  })
}

function loadWoodMaps() {
  const pending = Promise.all([
    loadTexture(diffUrl, THREE.SRGBColorSpace),
    loadTexture(norUrl, THREE.LinearSRGBColorSpace),
    loadTexture(roughUrl, THREE.LinearSRGBColorSpace)
  ])
  const timeout = new Promise<null>((resolve) => window.setTimeout(() => resolve(null), 1500))
  return Promise.race([pending, timeout]).catch(() => null)
}

function woodMaterial(maps: THREE.Texture[] | null, color: number) {
  const material = new THREE.MeshStandardMaterial({
    color,
    roughness: maps ? 1 : 0.82,
    metalness: 0.04,
    side: THREE.DoubleSide,
    map: maps?.[0],
    normalMap: maps?.[1],
    roughnessMap: maps?.[2]
  })
  return material
}

function buildCrate(logo: THREE.Texture, maps: THREE.Texture[] | null) {
  const root = new THREE.Group()
  const wood = woodMaterial(maps, 0xffffff)
  const palletMat = woodMaterial(maps, 0x8a6a4c)
  const metal = new THREE.MeshStandardMaterial({ color: 0x9aa1a8, roughness: 0.35, metalness: 0.72 })
  addBox(root, 1.7, 0.2, 1.7, 0, 0.1, 0, palletMat)
  addBox(root, CRATE_W, 0.08, CRATE_D, 0, FLOOR_Y - 0.04, 0, wood)

  const wallY = FLOOR_Y + CRATE_H / 2
  const t = 0.08
  addBox(root, t, CRATE_H, CRATE_D, -CRATE_W / 2 + t / 2, wallY, 0, wood)
  addBox(root, t, CRATE_H, CRATE_D, CRATE_W / 2 - t / 2, wallY, 0, wood)
  addBox(root, CRATE_W, CRATE_H, t, 0, wallY, -CRATE_D / 2 + t / 2, wood)
  addBox(root, CRATE_W, t, CRATE_D, 0, FLOOR_Y + CRATE_H - t / 2, 0, wood)

  const edge = 0.045
  const frontZ = CRATE_D / 2 + edge / 2
  const sideX = CRATE_W / 2 + edge / 2
  addBox(root, CRATE_W + edge, edge, edge, 0, FLOOR_Y + edge / 2, frontZ, metal)
  addBox(root, CRATE_W + edge, edge, edge, 0, FLOOR_Y + CRATE_H - edge / 2, frontZ, metal)
  addBox(root, edge, CRATE_H, edge, -CRATE_W / 2, wallY, frontZ, metal)
  addBox(root, edge, CRATE_H, edge, CRATE_W / 2, wallY, frontZ, metal)
  addBox(root, edge, edge, CRATE_D + edge, sideX, FLOOR_Y + edge / 2, 0, metal)
  addBox(root, edge, edge, CRATE_D + edge, sideX, FLOOR_Y + CRATE_H - edge / 2, 0, metal)
  addBox(root, edge, CRATE_H, edge, sideX, wallY, -CRATE_D / 2, metal)
  addBox(root, edge, CRATE_H, edge, sideX, wallY, CRATE_D / 2, metal)

  const logoMat = new THREE.MeshStandardMaterial({
    map: logo,
    transparent: true,
    depthWrite: false,
    roughness: 0.9,
    metalness: 0,
    polygonOffset: true,
    polygonOffsetFactor: -1,
    polygonOffsetUnits: -1
  })
  const logoMesh = new THREE.Mesh(new THREE.PlaneGeometry(1.22, 0.334), logoMat)
  logoMesh.position.set(CRATE_W / 2 + 0.03, wallY, 0)
  logoMesh.rotation.y = Math.PI / 2
  root.add(logoMesh)

  doorPivot = new THREE.Group()
  doorPivot.position.copy(hinge)
  const door = new THREE.Group()
  addBox(door, 1.34, PANEL_LEN, 0.07, 0, PANEL_LEN / 2, 0.035, wood)
  addBox(door, 1.4, edge, 0.09, 0, edge / 2, 0.05, metal)
  addBox(door, 1.4, edge, 0.09, 0, PANEL_LEN - edge / 2, 0.05, metal)
  addBox(door, edge, PANEL_LEN, 0.09, -0.67, PANEL_LEN / 2, 0.05, metal)
  addBox(door, edge, PANEL_LEN, 0.09, 0.67, PANEL_LEN / 2, 0.05, metal)
  doorPivot.add(door)
  root.add(doorPivot)
  return root
}

function buildTruck() {
  const root = new THREE.Group()
  const body = new THREE.MeshStandardMaterial({ color: 0x243056, roughness: 0.45, metalness: 0.35 })
  const weight = new THREE.MeshStandardMaterial({ color: 0x1a2238, roughness: 0.5, metalness: 0.4 })
  const cabinMat = new THREE.MeshStandardMaterial({ color: 0x2c3a66, roughness: 0.4, metalness: 0.3 })
  const glass = new THREE.MeshStandardMaterial({ color: 0x0d121c, roughness: 0.15, metalness: 0.1 })
  const steel = new THREE.MeshStandardMaterial({ color: 0x8b9098, roughness: 0.35, metalness: 0.7 })
  const forkMat = new THREE.MeshStandardMaterial({ color: 0x2a2a2a, roughness: 0.4, metalness: 0.6 })
  const tire = new THREE.MeshStandardMaterial({ color: 0x1a1a1a, roughness: 0.9 })
  const lampMat = new THREE.MeshStandardMaterial({ color: 0xfff4cc, emissive: 0xfff1c4, emissiveIntensity: 0.7 })

  addBox(root, 1.6, 0.42, 0.9, 0, 0.37, 0, body)
  addBox(root, 0.38, 0.5, 0.86, -0.72, 0.43, 0, weight)
  addBox(root, 0.62, 0.62, 0.7, 0.08, 0.86, 0, cabinMat)
  addBox(root, 0.04, 0.42, 0.56, 0.4, 0.92, 0, glass)

  screen = new THREE.Mesh(
    new THREE.PlaneGeometry(SCREEN_W, SCREEN_H),
    new THREE.MeshBasicMaterial({ color: 0x050806, toneMapped: false })
  )
  screen.position.set(0.43, 0.96, 0)
  screen.rotation.y = Math.PI / 2
  screen.castShadow = false
  root.add(screen)

  addBox(root, 0.08, 1.05, 0.08, 0.58, 0.68, 0.24, steel)
  addBox(root, 0.08, 1.05, 0.08, 0.58, 0.68, -0.24, steel)
  addBox(root, 0.08, 0.28, 0.55, 0.58, 0.4, 0, steel)
  addBox(root, 0.62, 0.035, 0.07, 0.9, 0.22, 0.16, forkMat)
  addBox(root, 0.62, 0.035, 0.07, 0.9, 0.22, -0.16, forkMat)
  addBox(root, 0.08, 0.42, 0.08, -0.1, 1.28, -0.28, steel)
  addBox(root, 0.08, 0.42, 0.08, -0.1, 1.28, 0.28, steel)
  addBox(root, 0.08, 0.42, 0.08, 0.28, 1.28, -0.28, steel)
  addBox(root, 0.08, 0.42, 0.08, 0.28, 1.28, 0.28, steel)
  addBox(root, 0.5, 0.04, 0.7, 0.1, 1.5, 0, steel)
  addBox(root, 0.08, 0.06, 0.1, 0.78, 0.42, 0.28, lampMat)
  addBox(root, 0.08, 0.06, 0.1, 0.78, 0.42, -0.28, lampMat)

  const wheelGeo = new THREE.CylinderGeometry(0.18, 0.18, 0.14, 18)
  wheelGeo.rotateZ(Math.PI / 2)
  for (const [x, z] of [[-0.52, 0.42], [-0.52, -0.42], [0.48, 0.42], [0.48, -0.42]] as const) {
    const wheel = new THREE.Mesh(wheelGeo, tire)
    wheel.position.set(x, 0.18, z)
    wheel.castShadow = true
    wheel.receiveShadow = true
    root.add(wheel)
  }

  root.scale.setScalar(TRUCK_SCALE)
  return root
}

function placeTruck(now: number) {
  if (!truck) return
  const d = distanceAt(now)
  let pitch = 0
  if (d <= FLOOR_LEN) {
    pose.set(0, FLOOR_Y, -0.1 + d)
    const along = d - (FLOOR_LEN - 0.25)
    if (along > 0) pitch = rampPitch * smooth(along, 0, 0.25)
  } else if (d <= FLOOR_LEN + RAMP_LEN) {
    const s = d - FLOOR_LEN
    rampPoint(s, pose)
    const leave = s - (RAMP_LEN - 0.25)
    pitch = leave > 0 ? rampPitch * (1 - smooth(leave, 0, 0.25)) : rampPitch
  } else {
    rampPoint(RAMP_LEN, rampTip)
    const s = Math.min(GROUND_LEN, d - FLOOR_LEN - RAMP_LEN)
    pose.set(0, 0, rampTip.z + s)
  }
  truck.position.copy(pose)
  face(pitch)
}

function aspectScale() {
  if (!camera || camera.aspect >= 1) return 1
  return Math.min(2.6, Math.max(1, 1.15 / camera.aspect))
}

function buildCameraPath() {
  if (!truck || !screen || !camera) return
  truck.position.set(0, 0, STOP_Z)
  face(0)
  truck.updateMatrixWorld(true)
  screen.getWorldPosition(screenCenter)
  normal.set(0, 0, 1).transformDirection(screen.matrixWorld)
  const fillDist = (SCREEN_H * TRUCK_SCALE) / 2 / Math.tan(THREE.MathUtils.degToRad(9))
  lookKeys[2].copy(screenCenter)
  lookKeys[3].copy(screenCenter)
  camKeys[3].copy(screenCenter).addScaledVector(normal, fillDist)
  const k = aspectScale()
  const cams = camKeys.map((point, index) => {
    if (index === 3) return point.clone()
    return point.clone().sub(lookKeys[index]).multiplyScalar(k).add(lookKeys[index])
  })
  camCurve = new THREE.CatmullRomCurve3(cams)
  lookCurve = new THREE.CatmullRomCurve3(lookKeys.map((point) => point.clone()))
}

function placeCamera(now: number) {
  if (!camera || !camCurve || !lookCurve) return
  const span = camTimes.length - 1
  let index = 0
  while (index < span - 1 && now > camTimes[index + 1]) index += 1
  const u = linear(now, camTimes[index], camTimes[index + 1])
  const point = camCurve.getPoint((index + u) / span)
  const look = lookCurve.getPoint((index + u) / span)
  camera.position.copy(point)
  camera.lookAt(look)
  camera.fov = now <= 5560 ? 38 : 38 + (18 - 38) * smooth(now, 5560, 7600)
  camera.updateProjectionMatrix()
}

function projectScreen(host: DOMRect) {
  if (!screen || !camera) return null
  const width = SCREEN_W
  const height = SCREEN_H
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  const local = [
    [-width / 2, -height / 2],
    [width / 2, -height / 2],
    [width / 2, height / 2],
    [-width / 2, height / 2]
  ]
  screen.updateWorldMatrix(true, false)
  for (const [x, y] of local) {
    projected.set(x, y, 0).applyMatrix4(screen.matrixWorld)
    projected.project(camera)
    if (projected.z < -1 || projected.z > 1) return null
    const px = (projected.x * 0.5 + 0.5) * host.width + host.left
    const py = (-projected.y * 0.5 + 0.5) * host.height + host.top
    minX = Math.min(minX, px)
    maxX = Math.max(maxX, px)
    minY = Math.min(minY, py)
    maxY = Math.max(maxY, py)
  }
  const rect = { left: minX, top: minY, width: maxX - minX, height: maxY - minY }
  if (rect.width < 8 || rect.height < 8 || rect.width > host.width * 2.4 || rect.height > host.height * 2.4) return null
  return rect
}

function layoutCard(now: number) {
  if (!card || !hostEl.value) return
  const show = now >= 5560
  bezelVisible.value = show
  if (!show) {
    card.style.opacity = '0'
    return
  }
  const host = hostEl.value.getBoundingClientRect()
  const rect = projectScreen(host)
  if (!rect) return
  const settle = smooth(now, 7600, 8000)
  const margin = Math.max(10, rect.width * 0.05)
  const name = Math.max(36, rect.height * 0.16)
  holeMargin.value = `${margin}px ${margin}px 0`
  bezelStyle.left = `${rect.left - margin}px`
  bezelStyle.top = `${rect.top - margin}px`
  bezelStyle.width = `${rect.width + margin * 2}px`
  bezelStyle.height = `${rect.height + margin + name}px`
  bezelStyle.opacity = String(smooth(now, 5560, 5900) * (1 - settle))
  holeHeight.value = `${rect.height}px`
  nameHeight.value = `${name}px`

  const hostLimit = Math.min((host.width * 0.92) / natural.width, (host.height * 0.78) / natural.height)
  const fit = Math.min(rect.width / natural.width, rect.height / natural.height, hostLimit)
  const scale = fit + (1 - fit) * settle
  const drawnW = natural.width * fit
  const drawnH = natural.height * fit
  const cardLeft = rect.left + (rect.width - drawnW) / 2
  const cardTop = rect.top + (rect.height - drawnH) / 2
  card.style.position = 'fixed'
  card.style.left = '0px'
  card.style.top = '0px'
  card.style.margin = '0'
  card.style.zIndex = '6'
  card.style.transformOrigin = '0 0'
  card.style.transform = `translate(${cardLeft + (natural.left - cardLeft) * settle}px, ${cardTop + (natural.top - cardTop) * settle}px) scale(${scale})`
  card.style.opacity = String(smooth(now, 5900, 6400))
}

function update(now: number) {
  if (!scene || !camera || !renderer || !doorPivot) return
  veil.value = 1 - smooth(now, 0, 1000)
  doorPivot.rotation.x = smooth(now, 1000, 1900) * DOOR_ANGLE
  placeTruck(now)
  placeCamera(now)
  layoutCard(now)
  renderer.render(scene, camera)
}

function resize() {
  if (!renderer || !camera || !hostEl.value) return
  const width = hostEl.value.clientWidth
  const height = hostEl.value.clientHeight
  if (width < 2 || height < 2) return
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.setSize(width, height, false)
  camera.aspect = width / height
  buildCameraPath()
  camera.updateProjectionMatrix()
}

function finish() {
  if (finished || stopped) return
  finished = true
  stopped = true
  if (raf) cancelAnimationFrame(raf)
  emit('done')
}

function fail() {
  if (finished || stopped) return
  stopped = true
  if (raf) cancelAnimationFrame(raf)
  emit('fallback')
}

function tick(now: number) {
  if (stopped) return
  raf = requestAnimationFrame(tick)
  const delta = now - lastTick
  lastTick = now
  if (document.hidden) {
    wasHidden = true
    return
  }
  if (wasHidden) {
    wasHidden = false
    return
  }
  elapsed = Math.min(INTRO_MS, elapsed + delta)
  update(elapsed)
  if (elapsed >= INTRO_MS) finish()
}

function loadLogo() {
  const canvas = document.createElement('canvas')
  canvas.width = 1024
  canvas.height = 280
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  logoTexture = texture
  const image = new Image()
  logoUrl = URL.createObjectURL(new Blob([lockupSvg()], { type: 'image/svg+xml' }))
  image.onload = () => {
    if (stopped) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(image, 0, 0, canvas.width, canvas.height)
    texture.needsUpdate = true
    URL.revokeObjectURL(logoUrl)
    logoUrl = ''
  }
  image.src = logoUrl
  return texture
}

function makeGradient() {
  const canvas = document.createElement('canvas')
  canvas.width = 2
  canvas.height = 256
  const ctx = canvas.getContext('2d')
  if (!ctx) return null
  const gradient = ctx.createLinearGradient(0, 0, 0, 256)
  gradient.addColorStop(0, '#14224f')
  gradient.addColorStop(0.55, '#0b1533')
  gradient.addColorStop(1, '#060a18')
  ctx.fillStyle = gradient
  ctx.fillRect(0, 0, 2, 256)
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  texture.magFilter = THREE.LinearFilter
  gradientTexture = texture
  return texture
}

function measureCard() {
  card = document.querySelector('[data-testid="boot-card"]')
  if (!card) return false
  const rect = card.getBoundingClientRect()
  if (rect.width < 2 || rect.height < 2) return false
  natural = { left: rect.left, top: rect.top, width: rect.width, height: rect.height }
  return true
}

function boot(maps: THREE.Texture[] | null) {
  const host = hostEl.value
  const canvas = canvasEl.value
  if (!host || !canvas || !measureCard()) {
    fail()
    return
  }
  woodTextures = maps ?? []
  let nextRenderer: THREE.WebGLRenderer
  try {
    nextRenderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false })
  } catch {
    fail()
    return
  }
  if (!nextRenderer.getContext()) {
    nextRenderer.dispose()
    fail()
    return
  }
  renderer = nextRenderer
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.05
  renderer.shadowMap.enabled = true
  renderer.shadowMap.type = THREE.PCFSoftShadowMap
  renderer.setClearColor(0x060a18, 1)
  scene = new THREE.Scene()
  scene.background = makeGradient()
  scene.fog = new THREE.Fog(0x060a18, 9, 24)
  pmrem = new THREE.PMREMGenerator(renderer)
  room = new RoomEnvironment()
  envTexture = pmrem.fromScene(room, 0.04).texture
  scene.environment = envTexture
  scene.environmentIntensity = 0.55
  camera = new THREE.PerspectiveCamera(38, 1, 0.05, 60)
  scene.add(new THREE.AmbientLight(0xffffff, 0.25))
  const spot = new THREE.SpotLight(0xfff4e0, 80, 20, Math.PI / 6, 0.65, 2)
  spot.position.set(0.6, 6.5, 2.4)
  spot.target.position.set(0.3, 0, 1.8)
  spot.castShadow = true
  spot.shadow.mapSize.set(1024, 1024)
  spot.shadow.camera.near = 0.5
  spot.shadow.camera.far = 20
  spot.shadow.bias = -0.0008
  scene.add(spot)
  scene.add(spot.target)
  const fill = new THREE.DirectionalLight(0xc5d4ff, 0.6)
  fill.position.set(0, 2, 8)
  scene.add(fill)
  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(40, 40),
    new THREE.MeshStandardMaterial({ color: 0x0b1430, roughness: 0.38, metalness: 0.25 })
  )
  ground.rotation.x = -Math.PI / 2
  ground.receiveShadow = true
  scene.add(ground)
  rampPoint(PANEL_LEN, rampTip)
  scene.add(buildCrate(loadLogo(), maps))
  truck = buildTruck()
  scene.add(truck)
  resize()
  resizeObserver = new ResizeObserver(() => resize())
  resizeObserver.observe(host)
  wasHidden = document.hidden
  lastTick = performance.now()
  raf = requestAnimationFrame(tick)
}

function dispose() {
  stopped = true
  if (raf) cancelAnimationFrame(raf)
  resizeObserver?.disconnect()
  resizeObserver = null
  if (logoUrl) URL.revokeObjectURL(logoUrl)
  logoTexture?.dispose()
  gradientTexture?.dispose()
  woodTextures.forEach((texture) => texture.dispose())
  envTexture?.dispose()
  pmrem?.dispose()
  room?.traverse((obj) => {
    const mesh = obj as THREE.Mesh
    if (!mesh.isMesh) return
    mesh.geometry?.dispose()
    const material = mesh.material
    if (Array.isArray(material)) material.forEach((item) => item.dispose())
    else material?.dispose()
  })
  if (scene) {
    scene.traverse((obj) => {
      const mesh = obj as THREE.Mesh
      if (!mesh.isMesh) return
      mesh.geometry?.dispose()
      const material = mesh.material
      if (Array.isArray(material)) material.forEach((item) => item.dispose())
      else material?.dispose()
    })
  }
  renderer?.dispose()
  renderer = null
  if (card) card.removeAttribute('style')
}

onMounted(() => {
  requestAnimationFrame(() => {
    if (stopped) return
    void loadWoodMaps().then((maps) => {
      if (stopped) {
        maps?.forEach((texture) => texture.dispose())
        return
      }
      boot(maps)
    })
  })
})

onBeforeUnmount(dispose)
</script>

<style scoped>
.intro {
  position: absolute;
  inset: 0;
  overflow: hidden;
  background: #060a18;
  z-index: 1;
}
canvas {
  width: 100%;
  height: 100%;
  display: block;
}
.veil {
  position: absolute;
  inset: 0;
  background: #060a18;
  pointer-events: none;
  z-index: 3;
}
.bezel {
  position: fixed;
  z-index: 4;
  pointer-events: none;
  display: flex;
  flex-direction: column;
  background: #10141c;
  box-sizing: border-box;
}
.bezel-hole {
  background: #050806;
}
.bezel-name {
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  padding: 0 8px;
}
</style>
