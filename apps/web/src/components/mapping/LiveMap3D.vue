<template>
  <div ref="host" class="cloud">
    <p v-if="fallback" class="fallback">当前环境没有 WebGL，点云计数仍会更新</p>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { disposeVehicle, loadVehicleModel, robotCenterZ, type VehicleModel } from '@/vehicle/model'
import { useRobotStore } from '@/stores/robot'

const props = defineProps<{ points: Float32Array }>()
const host = ref<HTMLDivElement | null>(null)
const fallback = ref(false)
let renderer: THREE.WebGLRenderer | null = null
let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | null = null
let cloud: THREE.Points | null = null
let controls: OrbitControls | null = null
let frame = 0
let fitted = false
let observer: ResizeObserver | null = null
let vehicle: VehicleModel | null = null
let closed = false
const robot = useRobotStore()

function draw() {
  if (!cloud || !camera || !controls) return
  const source = props.points
  const count = Math.floor(source.length / 4)
  const positions = new Float32Array(Math.max(count, 1) * 3)
  const colors = new Float32Array(Math.max(count, 1) * 3)
  let minZ = Infinity
  let maxZ = -Infinity
  for (let index = 0; index < count; index += 1) {
    const z = source[index * 4 + 2]
    positions[index * 3] = source[index * 4]
    positions[index * 3 + 1] = source[index * 4 + 1]
    positions[index * 3 + 2] = z
    if (z < minZ) minZ = z
    if (z > maxZ) maxZ = z
  }
  const span = maxZ - minZ || 1
  for (let index = 0; index < count; index += 1) {
    const height = (positions[index * 3 + 2] - minZ) / span
    colors[index * 3] = 0.4 + height * 0.6
    colors[index * 3 + 1] = 0.85
    colors[index * 3 + 2] = 0.2
  }
  cloud.geometry.dispose()
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
  geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3))
  cloud.geometry = geometry
  if (!fitted && count > 10) {
    geometry.computeBoundingSphere()
    const sphere = geometry.boundingSphere
    if (sphere) {
      const radius = sphere.radius || 5
      camera.position.copy(sphere.center).add(new THREE.Vector3(radius, radius, radius))
      controls.target.copy(sphere.center)
      controls.update()
      fitted = true
    }
  }
}

function resize() {
  const el = host.value
  if (!el || !renderer || !camera) return
  const width = el.clientWidth || 640
  const height = el.clientHeight || 360
  camera.aspect = width / Math.max(height, 1)
  camera.updateProjectionMatrix()
  renderer.setSize(width, height)
}

onMounted(() => {
  const el = host.value!
  scene = new THREE.Scene()
  camera = new THREE.PerspectiveCamera(50, 1, 0.05, 500)
  camera.up.set(0, 0, 1)
  camera.position.set(8, -8, 6)
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true })
  } catch {
    fallback.value = true
    return
  }
  renderer.setPixelRatio(window.devicePixelRatio || 1)
  el.appendChild(renderer.domElement)
  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  const material = new THREE.PointsMaterial({ size: 0.06, vertexColors: true })
  cloud = new THREE.Points(new THREE.BufferGeometry(), material)
  scene.add(cloud)
  scene.add(new THREE.AmbientLight(0xffffff, 0.65))
  const sun = new THREE.DirectionalLight(0xffffff, 1.1)
  sun.position.set(6, -4, 10)
  scene.add(sun)
  void loadVehicleModel().then((model) => {
    if (closed || !scene) {
      disposeVehicle(model.root)
      return
    }
    vehicle = model
    const front = Number(robot.paramsInfo.robot?.size_length_front?.default)
    const centerZ = robotCenterZ(model, Number.isFinite(front) ? front : 0)
    model.root.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(
      new THREE.Vector3(0, 1, 0),
      new THREE.Vector3(0, 0, 1),
      new THREE.Vector3(1, 0, 0)
    ))
    model.root.position.set(-centerZ, 0, -model.bounds.min.y)
    scene.add(model.root)
    if (!fitted) {
      const box = new THREE.Box3().setFromObject(model.root)
      const sphere = box.getBoundingSphere(new THREE.Sphere())
      const radius = sphere.radius || 3
      camera?.position.copy(sphere.center).add(new THREE.Vector3(radius, -radius, radius * 0.6))
      controls?.target.copy(sphere.center)
      controls?.update()
    }
  }).catch(() => {})
  resize()
  observer = new ResizeObserver(resize)
  observer.observe(el)
  const loop = () => {
    frame = requestAnimationFrame(loop)
    controls?.update()
    if (renderer && scene && camera) renderer.render(scene, camera)
  }
  loop()
  draw()
})
watch(() => props.points, draw)
onBeforeUnmount(() => {
  closed = true
  cancelAnimationFrame(frame)
  observer?.disconnect()
  controls?.dispose()
  if (vehicle) disposeVehicle(vehicle.root)
  renderer?.dispose()
})
</script>

<style scoped>
.cloud { width: 100%; height: 100%; min-height: 240px; position: relative; background: #0c120e; }
.fallback { margin: 16px; color: var(--hud-muted, #7f8f84); }
</style>
