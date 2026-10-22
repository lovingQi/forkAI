<template>
  <div ref="hostEl" class="intro" data-testid="boot-intro" aria-hidden="true">
    <canvas ref="canvasEl"></canvas>
    <div class="veil" :style="{ opacity: String(veil) }"></div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import * as THREE from 'three'
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js'
import diffUrl from '@/assets/boot/plywood_diff.jpg'
import norUrl from '@/assets/boot/plywood_nor.jpg'
import roughUrl from '@/assets/boot/plywood_rough.jpg'
import logoAssetUrl from '@/assets/boot/logo.png'
import { disposeVehicle, loadVehicleModel, type VehicleModel } from '@/vehicle/model'

const emit = defineEmits<{ done: []; fallback: [] }>()

const hostEl = ref<HTMLElement | null>(null)
const canvasEl = ref<HTMLCanvasElement | null>(null)
const veil = ref(1)

const INTRO_MS = 8000
const CRATE_W = 1.5
const CRATE_H = 1.6
const CRATE_D = 1.7
const FLOOR_Y = 0.28
const EDGE = 0.045
const DRIVE_Y = FLOOR_Y + EDGE
const PANEL_LEN = 1.6
const DOOR_ANGLE = Math.PI / 2 + Math.asin(DRIVE_Y / PANEL_LEN)
const TRUCK_SCALE = 0.55
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
let logoTexture: THREE.Texture | null = null
let screenMap: THREE.CanvasTexture | null = null
let screenDecal: THREE.Mesh | null = null
let faceAspect = 199 / 145
let resizeObserver: ResizeObserver | null = null
let card: HTMLElement | null = null
let natural = { left: 0, top: 0, width: 560, height: 180 }
let pmrem: THREE.PMREMGenerator | null = null
let envTexture: THREE.Texture | null = null
let room: THREE.Scene | null = null
let gradientTexture: THREE.CanvasTexture | null = null
let woodTextures: THREE.Texture[] = []
let vehicle: VehicleModel | null = null
let sigma0 = -0.7
let wheelbase = 0.8
let camCurve: THREE.CatmullRomCurve3 | null = null
let lookCurve: THREE.CatmullRomCurve3 | null = null

const inside = new THREE.Vector3(0, FLOOR_Y, -0.1)
const hinge = new THREE.Vector3(0, DRIVE_Y, CRATE_D / 2)
const rampTip = new THREE.Vector3()
const rampScratch = new THREE.Vector3()
const pose = new THREE.Vector3()
const lead = new THREE.Vector3()
const trail = new THREE.Vector3()
const nearFace = new THREE.Vector3()
const farFace = new THREE.Vector3()
const localCenter = new THREE.Vector3()
const faceCorner = new THREE.Vector3()
const screenCorners = [new THREE.Vector3(), new THREE.Vector3(), new THREE.Vector3(), new THREE.Vector3()]
const axisX = new THREE.Vector3(1, 0, 0)
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
  qPitch.setFromAxisAngle(axisX, pitch)
  truck.quaternion.copy(qPitch)
}

function surfacePoint(sigma: number, out: THREE.Vector3) {
  if (sigma <= 0) {
    out.set(0, DRIVE_Y, hinge.z + sigma)
    return
  }
  if (sigma <= PANEL_LEN) {
    rampPoint(sigma, out)
    return
  }
  rampPoint(PANEL_LEN, rampTip)
  out.set(0, 0, rampTip.z + (sigma - PANEL_LEN))
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
  const floorBottom = FLOOR_Y - 0.08
  const floorHeight = DRIVE_Y - floorBottom
  addBox(root, CRATE_W, floorHeight, CRATE_D, 0, floorBottom + floorHeight / 2, 0, wood)

  const wallY = FLOOR_Y + CRATE_H / 2
  const t = 0.08
  addBox(root, t, CRATE_H, CRATE_D, -CRATE_W / 2 + t / 2, wallY, 0, wood)
  addBox(root, t, CRATE_H, CRATE_D, CRATE_W / 2 - t / 2, wallY, 0, wood)
  addBox(root, CRATE_W, CRATE_H, t, 0, wallY, -CRATE_D / 2 + t / 2, wood)
  addBox(root, CRATE_W, t, CRATE_D, 0, FLOOR_Y + CRATE_H - t / 2, 0, wood)

  const frontZ = CRATE_D / 2 + EDGE / 2
  const sideX = CRATE_W / 2 + EDGE / 2
  addBox(root, CRATE_W + EDGE, EDGE, EDGE, 0, DRIVE_Y - EDGE / 2, CRATE_D / 2 - EDGE / 2, metal)
  addBox(root, CRATE_W + EDGE, EDGE, EDGE, 0, FLOOR_Y + CRATE_H - EDGE / 2, frontZ, metal)
  addBox(root, EDGE, CRATE_H, EDGE, -CRATE_W / 2, wallY, frontZ, metal)
  addBox(root, EDGE, CRATE_H, EDGE, CRATE_W / 2, wallY, frontZ, metal)
  addBox(root, EDGE, EDGE, CRATE_D + EDGE, sideX, FLOOR_Y + EDGE / 2, 0, metal)
  addBox(root, EDGE, EDGE, CRATE_D + EDGE, sideX, FLOOR_Y + CRATE_H - EDGE / 2, 0, metal)
  addBox(root, EDGE, CRATE_H, EDGE, sideX, wallY, -CRATE_D / 2, metal)
  addBox(root, EDGE, CRATE_H, EDGE, sideX, wallY, CRATE_D / 2, metal)

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
  const logoMesh = new THREE.Mesh(new THREE.PlaneGeometry(1.22, (1.22 * 72) / 603), logoMat)
  logoMesh.position.set(CRATE_W / 2 + 0.03, wallY, 0)
  logoMesh.rotation.y = Math.PI / 2
  root.add(logoMesh)

  doorPivot = new THREE.Group()
  doorPivot.position.copy(hinge)
  const door = new THREE.Group()
  addBox(door, 1.34, PANEL_LEN, 0.07, 0, PANEL_LEN / 2, 0.035, wood)
  addBox(door, 1.4, EDGE, 0.09, 0, EDGE / 2, 0.05, metal)
  addBox(door, 1.4, EDGE, 0.09, 0, PANEL_LEN - EDGE / 2, 0.05, metal)
  addBox(door, EDGE, PANEL_LEN, 0.09, -0.67, PANEL_LEN / 2, 0.05, metal)
  addBox(door, EDGE, PANEL_LEN, 0.09, 0.67, PANEL_LEN / 2, 0.05, metal)
  doorPivot.add(door)
  root.add(doorPivot)
  return root
}

function mountModel(model: VehicleModel) {
  vehicle = model
  const root = new THREE.Group()
  const mid = (model.loadWheelZ + model.driveWheelZ) / 2
  model.root.position.set(0, -model.bounds.min.y, -mid)
  root.add(model.root)
  root.scale.setScalar(TRUCK_SCALE)
  model.root.traverse((child) => {
    const mesh = child as THREE.Mesh
    if (!mesh.isMesh) return
    mesh.castShadow = true
    mesh.receiveShadow = true
  })
  const panel = model.screen
  panel.material = new THREE.MeshBasicMaterial({ color: 0x050806, toneMapped: false })
  panel.castShadow = false
  panel.receiveShadow = false
  screen = panel
  const decalGeometry = panel.geometry.clone()
  assignScreenUvs(decalGeometry)
  screenMap = paintScreenCard()
  const decalMaterial = new THREE.MeshBasicMaterial({
    map: screenMap,
    transparent: true,
    toneMapped: false,
    depthWrite: false,
    polygonOffset: true,
    polygonOffsetFactor: -1,
    polygonOffsetUnits: -1
  })
  screenDecal = new THREE.Mesh(decalGeometry, decalMaterial)
  panel.add(screenDecal)
  wheelbase = Math.abs(model.driveWheelZ - model.loadWheelZ) * TRUCK_SCALE
  const tail = (model.bounds.max.z - mid) * TRUCK_SCALE
  const fork = (mid - model.bounds.min.z) * TRUCK_SCALE
  const margin = 0.04
  const minSigma = -CRATE_D + margin + fork
  const maxSigma = -tail - margin
  if (minSigma > maxSigma) throw new Error('木箱装不下这台车')
  sigma0 = (minSigma + maxSigma) / 2
  const forkTip = sigma0 + FLOOR_LEN + RAMP_LEN + GROUND_LEN - fork
  if (forkTip < PANEL_LEN + 0.3) throw new Error('坡道停靠距离不够')
  return root
}

function faceAxes(size: THREE.Vector3) {
  const thin = size.x <= size.y && size.x <= size.z ? 0 : size.y <= size.z ? 1 : 2
  const rest = [0, 1, 2].filter((axis) => axis !== thin)
  if (rest.includes(0) && rest.includes(1)) return { thin, u: 0, v: 1 }
  if (rest.includes(0)) return { thin, u: 0, v: rest.find((axis) => axis !== 0) ?? 2 }
  return { thin, u: 1, v: 2 }
}

function assignScreenUvs(geometry: THREE.BufferGeometry) {
  const position = geometry.getAttribute('position')
  geometry.computeBoundingBox()
  const box = geometry.boundingBox
  if (!box) return
  const size = box.getSize(new THREE.Vector3())
  const { u, v } = faceAxes(size)
  const uvs = new Float32Array(position.count * 2)
  const spanU = size.getComponent(u) || 1
  const spanV = size.getComponent(v) || 1
  for (let index = 0; index < position.count; index += 1) {
    const point = [position.getX(index), position.getY(index), position.getZ(index)]
    uvs[index * 2] = (point[u] - box.min.getComponent(u)) / spanU
    uvs[index * 2 + 1] = (point[v] - box.min.getComponent(v)) / spanV
  }
  geometry.setAttribute('uv', new THREE.BufferAttribute(uvs, 2))
}

function paintScreenCard() {
  const canvas = document.createElement('canvas')
  canvas.width = 1024
  canvas.height = 746
  const ctx = canvas.getContext('2d')
  const scale = 1024 / 560
  if (ctx) {
    ctx.fillStyle = '#050806'
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.strokeStyle = 'rgba(182, 255, 59, 0.55)'
    ctx.lineWidth = scale
    ctx.strokeRect(scale / 2, scale / 2, canvas.width - scale, canvas.height - scale)
    const pad = 28 * scale
    ctx.fillStyle = '#b6ff3b'
    ctx.font = `${28 * scale}px Consolas, monospace`
    ctx.letterSpacing = `${0.2 * 28 * scale}px`
    ctx.textBaseline = 'top'
    ctx.fillText('EASYFORK', pad, pad)
    ctx.letterSpacing = '0px'
    ctx.fillStyle = '#e8f5e1'
    ctx.font = `${16 * scale}px Consolas, monospace`
    const lines = ['[ .. ] core', '[ .. ] jarvis', '[ .. ] rosbridge']
    let y = pad + 28 * scale + 16 * scale
    for (const line of lines) {
      ctx.fillText(line, pad, y)
      y += 16 * scale + 12 * scale
    }
  }
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  texture.anisotropy = 8
  return texture
}

function screenFace() {
  const panel = screen
  if (!panel || !truck) return false
  const geometry = panel.geometry
  if (!geometry.boundingBox) geometry.computeBoundingBox()
  const box = geometry.boundingBox
  if (!box) return false
  const size = box.getSize(faceCorner)
  const { thin, u, v } = faceAxes(size)
  const min = [box.min.x, box.min.y, box.min.z]
  const max = [box.max.x, box.max.y, box.max.z]
  const center = box.getCenter(localCenter)
  panel.updateWorldMatrix(true, false)
  nearFace.copy(center).setComponent(thin, min[thin]).applyMatrix4(panel.matrixWorld)
  farFace.copy(center).setComponent(thin, max[thin]).applyMatrix4(panel.matrixWorld)
  const forward = trail.set(0, 0, 1).transformDirection(truck.matrixWorld)
  const useFar = farFace.dot(forward) >= nearFace.dot(forward)
  const front = useFar ? max[thin] : min[thin]
  const localCorners = [
    [min[u], max[v]],
    [max[u], max[v]],
    [max[u], min[v]],
    [min[u], min[v]]
  ]
  localCorners.forEach(([faceU, faceV], index) => {
    const corner = screenCorners[index]
    corner.copy(center)
    corner.setComponent(thin, front)
    corner.setComponent(u, faceU)
    corner.setComponent(v, faceV)
    corner.applyMatrix4(panel.matrixWorld)
  })
  faceAspect = screenCorners[0].distanceTo(screenCorners[1]) / Math.max(screenCorners[0].distanceTo(screenCorners[3]), 1e-6)
  screenCenter.copy(screenCorners[0]).add(screenCorners[1]).add(screenCorners[2]).add(screenCorners[3]).multiplyScalar(0.25)
  normal.copy(screenCenter).sub(useFar ? nearFace : farFace).normalize()
  return true
}

function placeTruck(now: number) {
  if (!truck) return
  const sigma = sigma0 + distanceAt(now)
  surfacePoint(sigma + wheelbase / 2, lead)
  surfacePoint(sigma - wheelbase / 2, trail)
  pose.addVectors(lead, trail).multiplyScalar(0.5)
  const pitch = -Math.atan2(lead.y - trail.y, lead.z - trail.z)
  truck.position.copy(pose)
  face(pitch)
}

function aspectScale() {
  if (!camera || camera.aspect >= 1) return 1
  return Math.min(2.6, Math.max(1, 1.15 / camera.aspect))
}

function buildCameraPath() {
  if (!truck || !screen || !camera) return
  placeTruck(5560)
  truck.updateMatrixWorld(true)
  if (!screenFace()) return
  const top = Math.max(...screenCorners.map((corner) => corner.y))
  const bottom = Math.min(...screenCorners.map((corner) => corner.y))
  const fillDist = (top - bottom) / 2 / Math.tan(THREE.MathUtils.degToRad(9))
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
  if (!screen || !camera || !screenFace()) return null
  const points: { x: number; y: number }[] = []
  for (const corner of screenCorners) {
    projected.copy(corner).project(camera)
    if (projected.z < -1 || projected.z > 1) return null
    points.push({
      x: (projected.x * 0.5 + 0.5) * host.width + host.left,
      y: (-projected.y * 0.5 + 0.5) * host.height + host.top
    })
  }
  const xs = points.map((point) => point.x)
  const ys = points.map((point) => point.y)
  const width = Math.max(...xs) - Math.min(...xs)
  const height = Math.max(...ys) - Math.min(...ys)
  if (width < 8 || height < 8 || width > host.width * 2.4 || height > host.height * 2.4) return null
  return points
}

function adjugate(m: number[][]) {
  return [
    [m[1][1] * m[2][2] - m[1][2] * m[2][1], m[0][2] * m[2][1] - m[0][1] * m[2][2], m[0][1] * m[1][2] - m[0][2] * m[1][1]],
    [m[1][2] * m[2][0] - m[1][0] * m[2][2], m[0][0] * m[2][2] - m[0][2] * m[2][0], m[0][2] * m[1][0] - m[0][0] * m[1][2]],
    [m[1][0] * m[2][1] - m[1][1] * m[2][0], m[0][1] * m[2][0] - m[0][0] * m[2][1], m[0][0] * m[1][1] - m[0][1] * m[1][0]]
  ]
}

function multiply(a: number[][], b: number[][]) {
  const out = [0, 1, 2].map(() => [0, 0, 0])
  for (let row = 0; row < 3; row += 1) {
    for (let col = 0; col < 3; col += 1) {
      out[row][col] = a[row][0] * b[0][col] + a[row][1] * b[1][col] + a[row][2] * b[2][col]
    }
  }
  return out
}

function basis(x1: number, y1: number, x2: number, y2: number, x3: number, y3: number, x4: number, y4: number) {
  const source = [
    [x1, x2, x3],
    [y1, y2, y3],
    [1, 1, 1]
  ]
  const inverted = adjugate(source)
  const scale = [
    inverted[0][0] * x4 + inverted[0][1] * y4 + inverted[0][2],
    inverted[1][0] * x4 + inverted[1][1] * y4 + inverted[1][2],
    inverted[2][0] * x4 + inverted[2][1] * y4 + inverted[2][2]
  ]
  return multiply(source, [
    [scale[0], 0, 0],
    [0, scale[1], 0],
    [0, 0, scale[2]]
  ])
}

function pinMatrix(width: number, height: number, topLeft: { x: number; y: number }, topRight: { x: number; y: number }, bottomLeft: { x: number; y: number }, bottomRight: { x: number; y: number }) {
  const from = basis(0, 0, width, 0, 0, height, width, height)
  const to = basis(topLeft.x, topLeft.y, topRight.x, topRight.y, bottomLeft.x, bottomLeft.y, bottomRight.x, bottomRight.y)
  const homography = multiply(to, adjugate(from))
  const unit = homography[2][2] || 1
  for (const row of homography) {
    row[0] /= unit
    row[1] /= unit
    row[2] /= unit
  }
  const t = homography
  return `matrix3d(${t[0][0]},${t[1][0]},0,${t[2][0]},${t[0][1]},${t[1][1]},0,${t[2][1]},0,0,1,0,${t[0][2]},${t[1][2]},0,${t[2][2]})`
}

function layoutCard(now: number) {
  if (!card || !hostEl.value) return
  if (now < 5900) {
    card.style.opacity = '0'
    return
  }
  const host = hostEl.value.getBoundingClientRect()
  const projectedCorners = projectScreen(host)
  if (!projectedCorners) return
  const settle = now >= 7600 ? smooth(now, 7600, 8000) : 0
  const pinnedWidth = 560
  const pinnedHeight = 560 / faceAspect
  const width = pinnedWidth + (natural.width - pinnedWidth) * settle
  const height = pinnedHeight + (natural.height - pinnedHeight) * settle
  const resting = [
    { x: natural.left, y: natural.top },
    { x: natural.left + natural.width, y: natural.top },
    { x: natural.left + natural.width, y: natural.top + natural.height },
    { x: natural.left, y: natural.top + natural.height }
  ]
  const corners = projectedCorners.map((point, index) => ({
    x: point.x + (resting[index].x - point.x) * settle,
    y: point.y + (resting[index].y - point.y) * settle
  }))
  card.style.position = 'fixed'
  card.style.left = '0px'
  card.style.top = '0px'
  card.style.margin = '0'
  card.style.zIndex = '6'
  card.style.transformOrigin = '0 0'
  card.style.boxSizing = 'border-box'
  card.style.width = `${width}px`
  card.style.height = `${height}px`
  card.style.transform = pinMatrix(width, height, corners[0], corners[1], corners[3], corners[2])
  card.style.opacity = String(smooth(now, 5900, 6400))
}

function update(now: number) {
  if (!scene || !camera || !renderer || !doorPivot) return
  veil.value = 1 - smooth(now, 0, 1000)
  doorPivot.rotation.x = smooth(now, 1000, 1900) * DOOR_ANGLE
  placeTruck(now)
  placeCamera(now)
  layoutCard(now)
  const decalMaterial = screenDecal?.material
  if (decalMaterial && !Array.isArray(decalMaterial)) {
    decalMaterial.opacity = now <= 6400 ? 1 : now >= 7000 ? 0 : 1 - smooth(now, 6400, 7000)
  }
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
  const texture = new THREE.Texture()
  texture.colorSpace = THREE.SRGBColorSpace
  if (renderer) texture.anisotropy = renderer.capabilities.getMaxAnisotropy()
  logoTexture = texture
  const image = new Image()
  image.onload = () => {
    if (stopped) return
    texture.image = image
    texture.needsUpdate = true
  }
  image.src = logoAssetUrl
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

function boot(maps: THREE.Texture[] | null, model: VehicleModel) {
  vehicle = model
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
  if (!vehicle) {
    fail()
    return
  }
  try {
    truck = mountModel(vehicle)
  } catch {
    fail()
    return
  }
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
  if (vehicle && !vehicle.root.parent) disposeVehicle(vehicle.root)
  if (raf) cancelAnimationFrame(raf)
  resizeObserver?.disconnect()
  resizeObserver = null
  logoTexture?.dispose()
  screenMap?.dispose()
  screenDecal?.geometry.dispose()
  const decalMaterial = screenDecal?.material
  if (decalMaterial && !Array.isArray(decalMaterial)) decalMaterial.dispose()
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
    const modelTask = Promise.race([
      loadVehicleModel(),
      new Promise<VehicleModel>((_, reject) => window.setTimeout(() => reject(new Error('车辆模型加载超时')), 10000))
    ])
    void Promise.all([loadWoodMaps(), modelTask]).then(([maps, model]) => {
      if (stopped) {
        maps?.forEach((texture) => texture.dispose())
        disposeVehicle(model.root)
        return
      }
      try {
        boot(maps, model)
      } catch {
        disposeVehicle(model.root)
        fail()
      }
    }).catch(() => {
      if (!stopped) fail()
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
</style>
