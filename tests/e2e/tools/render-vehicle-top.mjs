import { chromium } from 'playwright'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(here, '../../..')
const threeRoot = path.join(root, 'node_modules/three')
const modelPath = path.join(root, 'apps/web/src/assets/vehicle/r15-forklift.glb')
const outDir = path.join(root, 'apps/web/src/assets/vehicle')

const pageSource = `<!doctype html>
<html><body><canvas id="c"></canvas>
<script type="importmap">
{ "imports": {
  "three": "/three/build/three.module.js",
  "three/addons/": "/three/examples/jsm/"
} }
</script>
<script type="module">
import * as THREE from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js'

const TAIL_LIMIT_Z = 1.686
const MARGIN = 0.05
const PX = 400
const loader = new GLTFLoader()
loader.setMeshoptDecoder(MeshoptDecoder)
const response = await fetch('/model.glb')
if (!response.ok) throw new Error('模型请求失败')
const bytes = await response.arrayBuffer()
const gltf = await new Promise((resolve, reject) => loader.parse(bytes, '', resolve, reject))
const model = gltf.scene
const cameras = []
model.traverse((child) => { if (child.isCamera) cameras.push(child) })
cameras.forEach((camera) => camera.removeFromParent())
model.updateWorldMatrix(true, true)
model.traverse((child) => {
  if (!child.isMesh) return
  const box = new THREE.Box3().setFromObject(child)
  const unnamed = !child.userData.name && /^mesh_\\d+(_instance_\\d+)?$/.test(child.name)
  if (unnamed && box.max.z > TAIL_LIMIT_Z + MARGIN) child.visible = false
})
const bounds = new THREE.Box3()
model.traverse((child) => {
  if (!child.isMesh || !child.visible) return
  if (!child.geometry.boundingBox) child.geometry.computeBoundingBox()
  bounds.union(child.geometry.boundingBox.clone().applyMatrix4(child.matrixWorld))
})
const xMin = bounds.min.x
const xMax = bounds.max.x
const zMin = bounds.min.z
const zMax = bounds.max.z
const width = Math.max(1, Math.round((zMax - zMin) * PX))
const height = Math.max(1, Math.round((xMax - xMin) * PX))
const canvas = document.getElementById('c')
canvas.width = width
canvas.height = height
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true })
renderer.setSize(width, height, false)
renderer.setClearColor(0x000000, 0)
renderer.outputColorSpace = THREE.SRGBColorSpace
const scene = new THREE.Scene()
scene.add(model)
scene.add(new THREE.AmbientLight(0xffffff, 0.7))
const sun = new THREE.DirectionalLight(0xffffff, 1.4)
sun.position.set(2, 6, 3)
scene.add(sun)
const center = new THREE.Vector3((xMin + xMax) / 2, (bounds.min.y + bounds.max.y) / 2, (zMin + zMax) / 2)
const camera = new THREE.OrthographicCamera(-(zMax - zMin) / 2, (zMax - zMin) / 2, (xMax - xMin) / 2, -(xMax - xMin) / 2, 0.01, 40)
camera.position.set(center.x, center.y + 12, center.z)
camera.up.set(1, 0, 0)
camera.lookAt(center)
renderer.render(scene, camera)
window.__top = {
  pxPerM: width / (zMax - zMin),
  zMin, zMax, xMin, xMax,
  width, height,
  png: canvas.toDataURL('image/png')
}
</script></body></html>`

const served = []
const browser = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader'] })
const page = await browser.newPage()
await page.route('**/*', async (route) => {
  const url = new URL(route.request().url())
  if (url.pathname === '/index.html') {
    await route.fulfill({ contentType: 'text/html', body: pageSource })
    return
  }
  if (url.pathname === '/model.glb') {
    const body = fs.readFileSync(modelPath)
    served.push(`200 /model.glb ${body.length}`)
    await route.fulfill({ contentType: 'model/gltf-binary', body })
    return
  }
  if (url.pathname.startsWith('/three/')) {
    const file = path.join(threeRoot, decodeURIComponent(url.pathname.slice('/three/'.length)))
    if (!file.startsWith(threeRoot) || !fs.existsSync(file)) {
      served.push(`404 ${url.pathname}`)
      await route.fulfill({ status: 404, body: file })
      return
    }
    served.push(`200 ${url.pathname}`)
    const type = file.endsWith('.js') ? 'text/javascript' : 'application/octet-stream'
    await route.fulfill({ contentType: type, body: fs.readFileSync(file) })
    return
  }
  await route.fulfill({ status: 404, body: url.pathname })
})
const messages = []
page.on('pageerror', (error) => messages.push(`pageerror ${error}`))
page.on('console', (msg) => messages.push(`${msg.type()} ${msg.text()}`))
page.on('requestfailed', (request) => messages.push(`failed ${request.url()} ${request.failure()?.errorText}`))
try {
  await page.goto('http://vehicle.local/index.html', { waitUntil: 'networkidle', timeout: 20000 })
  await page.waitForFunction(() => window.__top, null, { timeout: 30000 })
} catch (error) {
  console.log(served.join('\n'))
  console.log(messages.join('\n'))
  throw error
}
const top = await page.evaluate(() => {
  const { png, ...meta } = window.__top
  return { meta, png }
})
await browser.close()
if (messages.length) console.log(messages.join('\n'))
const png = Buffer.from(top.png.replace(/^data:image\/png;base64,/, ''), 'base64')
fs.mkdirSync(outDir, { recursive: true })
fs.writeFileSync(path.join(outDir, 'top.png'), png)
const meta = {
  pxPerM: top.meta.pxPerM,
  zMin: top.meta.zMin,
  zMax: top.meta.zMax,
  xMin: top.meta.xMin,
  xMax: top.meta.xMax
}
fs.writeFileSync(path.join(outDir, 'top.json'), JSON.stringify(meta, null, 2) + '\n')
console.log(JSON.stringify({ ...meta, width: top.meta.width, height: top.meta.height }))
