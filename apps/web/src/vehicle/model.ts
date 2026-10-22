import * as THREE from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js'
import modelUrl from '@/assets/vehicle/r15-forklift.glb?url'

export const SCREEN_NODE = 'MT070H-BLANK-JW001 触摸屏-1'
export const LOAD_WHEEL_NODE = 'R1500-15-000 负载轮安装'
export const DRIVE_WHEEL_NODE = 'HL254-AC2500-48-19-SM05 舵轮总成-1'
export const TAIL_LIMIT_Z = 1.686
export const MISPLACED_MARGIN = 0.05
export const FALLBACK_LOAD_WHEEL_Z = 0
export const FALLBACK_DRIVE_WHEEL_Z = 1.435

export interface VehicleModel {
  root: THREE.Group
  bounds: THREE.Box3
  screen: THREE.Mesh
  loadWheelZ: number
  driveWheelZ: number
}

let cachedBytes: Promise<ArrayBuffer> | null = null

function bytes(): Promise<ArrayBuffer> {
  if (!cachedBytes) {
    cachedBytes = fetch(modelUrl).then((response) => {
      if (!response.ok) throw new Error(`车辆模型加载失败：${response.status}`)
      return response.arrayBuffer()
    })
  }
  return cachedBytes.then((buffer) => buffer.slice(0))
}

function named(root: THREE.Object3D, name: string): THREE.Object3D {
  let found: THREE.Object3D | null = null
  root.traverse((child) => {
    if (found) return
    if (child.name === name || child.userData.name === name) found = child
  })
  if (!found) throw new Error(`车辆模型缺少 ${name}`)
  return found
}

function worldBox(object: THREE.Object3D): THREE.Box3 {
  const box = new THREE.Box3()
  object.updateWorldMatrix(true, true)
  object.traverse((child) => {
    const mesh = child as THREE.Mesh
    if (!mesh.isMesh || !mesh.visible || !mesh.geometry) return
    if (!mesh.geometry.boundingBox) mesh.geometry.computeBoundingBox()
    const next = mesh.geometry.boundingBox!.clone().applyMatrix4(mesh.matrixWorld)
    box.union(next)
  })
  return box
}

function isMisplaced(mesh: THREE.Mesh, box: THREE.Box3): boolean {
  // GLTFLoader 给没有名字的网格补 mesh_序号，原始名字只在 userData.name。
  const unnamed = !mesh.userData.name && /^mesh_\d+(_instance_\d+)?$/.test(mesh.name)
  return unnamed && box.max.z > TAIL_LIMIT_Z + MISPLACED_MARGIN
}

function thinnestMesh(object: THREE.Object3D): THREE.Mesh {
  let chosen: THREE.Mesh | null = null
  let thickness = Infinity
  object.traverse((child) => {
    const mesh = child as THREE.Mesh
    if (!mesh.isMesh) return
    const size = new THREE.Box3().setFromObject(mesh).getSize(new THREE.Vector3())
    const thin = Math.min(size.x, size.y, size.z)
    if (thin < thickness) {
      thickness = thin
      chosen = mesh
    }
  })
  if (!chosen) throw new Error(`车辆模型缺少 ${SCREEN_NODE} 的屏幕面板`)
  return chosen
}

export async function loadVehicleModel(): Promise<VehicleModel> {
  const loader = new GLTFLoader()
  loader.setMeshoptDecoder(MeshoptDecoder)
  const gltf = await loader.parseAsync(await bytes(), '')
  const root = gltf.scene
  const cameras: THREE.Object3D[] = []
  root.traverse((child) => {
    const camera = child as THREE.Object3D & { isCamera?: boolean }
    if (camera.isCamera) cameras.push(camera)
  })
  cameras.forEach((camera) => camera.removeFromParent())
  root.updateWorldMatrix(true, true)
  root.traverse((child) => {
    const mesh = child as THREE.Mesh
    if (!mesh.isMesh) return
    if (isMisplaced(mesh, new THREE.Box3().setFromObject(mesh))) mesh.visible = false
  })
  const bounds = worldBox(root)
  const loadBox = worldBox(named(root, LOAD_WHEEL_NODE))
  const driveBox = worldBox(named(root, DRIVE_WHEEL_NODE))
  return {
    root,
    bounds,
    screen: thinnestMesh(named(root, SCREEN_NODE)),
    loadWheelZ: loadBox.isEmpty() ? FALLBACK_LOAD_WHEEL_Z : (loadBox.min.z + loadBox.max.z) / 2,
    driveWheelZ: driveBox.isEmpty() ? FALLBACK_DRIVE_WHEEL_Z : (driveBox.min.z + driveBox.max.z) / 2
  }
}

export function disposeVehicle(root: THREE.Object3D) {
  root.traverse((child) => {
    const mesh = child as THREE.Mesh
    if (!mesh.isMesh) return
    mesh.geometry?.dispose()
    const material = mesh.material
    const materials = Array.isArray(material) ? material : [material]
    materials.forEach((item) => item?.dispose())
  })
}

export function robotCenterZ(model: VehicleModel, lengthFrontMm: number): number {
  if (lengthFrontMm > 0) return model.bounds.max.z - lengthFrontMm / 1000
  return (model.loadWheelZ + model.driveWheelZ) / 2
}

export function robotToScene(xMm: number, yMm: number, heightM: number, out = new THREE.Vector3()): THREE.Vector3 {
  return out.set(yMm / 1000, heightM, xMm / 1000)
}
