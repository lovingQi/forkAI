import topUrl from '@/assets/vehicle/top.png'
import meta from '@/assets/vehicle/top.json'
import { FALLBACK_DRIVE_WHEEL_Z, FALLBACK_LOAD_WHEEL_Z } from './model'

export const TOP_VIEW = meta

let pending: Promise<HTMLImageElement> | null = null

export function vehicleTopImage(): Promise<HTMLImageElement> {
  if (!pending) {
    pending = new Promise((resolve, reject) => {
      const image = new Image()
      image.onload = () => resolve(image)
      image.onerror = () => reject(new Error('俯视图加载失败'))
      image.src = topUrl
    })
  }
  return pending
}

export function topCenterZ(lengthFrontMm: number): number {
  if (lengthFrontMm > 0) return TOP_VIEW.zMax - lengthFrontMm / 1000
  return (FALLBACK_LOAD_WHEEL_Z + FALLBACK_DRIVE_WHEEL_Z) / 2
}
