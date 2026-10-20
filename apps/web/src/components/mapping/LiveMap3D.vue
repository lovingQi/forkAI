<template>
  <div ref="host" class="cloud"></div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as THREE from 'three'

const props = defineProps<{ points: number[] }>()
const host = ref<HTMLDivElement | null>(null)
let renderer: THREE.WebGLRenderer | null = null
let scene: THREE.Scene | null = null
let camera: THREE.PerspectiveCamera | null = null
let cloud: THREE.Points | null = null
let frame = 0

function draw() {
  if (!scene || !camera || !renderer || !cloud) return
  const count = Math.floor(props.points.length / 3)
  const positions = new Float32Array(Math.max(count, 1) * 3)
  if (count) positions.set(props.points.slice(0, count * 3))
  else positions.set([0, 0, 0])
  cloud.geometry.dispose()
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
  cloud.geometry = geometry
  renderer.render(scene, camera)
}

onMounted(() => {
  const el = host.value!
  scene = new THREE.Scene()
  camera = new THREE.PerspectiveCamera(50, 1, 0.01, 500)
  camera.position.set(4, 4, 4)
  camera.lookAt(0, 0, 0)
  renderer = new THREE.WebGLRenderer({ antialias: true })
  renderer.setSize(el.clientWidth || 640, el.clientHeight || 360)
  el.appendChild(renderer.domElement)
  const material = new THREE.PointsMaterial({ color: 0xb6ff3b, size: 0.05 })
  cloud = new THREE.Points(new THREE.BufferGeometry(), material)
  scene.add(cloud)
  const loop = () => {
    frame = requestAnimationFrame(loop)
    if (renderer && scene && camera) renderer.render(scene, camera)
  }
  loop()
  draw()
})
watch(() => props.points, draw)
onBeforeUnmount(() => {
  cancelAnimationFrame(frame)
  renderer?.dispose()
})
</script>

<style scoped>
.cloud { width: 100%; height: 100%; min-height: 240px; }
</style>
