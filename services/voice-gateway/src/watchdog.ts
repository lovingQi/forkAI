import type { JarvisClient } from './jarvis.js'

export type WatchdogStopHandler = () => void

export class MotionWatchdog {
  private timer: NodeJS.Timeout | null = null
  private moving = false
  speed: number

  constructor(
    private jarvis: JarvisClient,
    private watchdogMs: number,
    private maxSpeed: number,
    defaultSpeed: number,
    private onStop?: WatchdogStopHandler
  ) {
    this.speed = defaultSpeed
  }

  setSpeed(v: number) {
    this.speed = Math.max(5, Math.min(this.maxSpeed, Math.round(v)))
  }

  bumpSpeed(delta: number) {
    this.setSpeed(this.speed + delta)
  }

  async drive(trans: number, rot: number) {
    await this.jarvis.control('drive', {
      trans,
      rot,
      speed: this.speed
    })
    this.moving = true
    this.resetTimer()
  }

  async stop(fromWatchdog = false) {
    this.clearTimer()
    this.moving = false
    await this.jarvis.control('stop')
    if (fromWatchdog && this.onStop) this.onStop()
  }

  private resetTimer() {
    this.clearTimer()
    this.timer = setTimeout(() => {
      this.stop(true).catch(() => {})
    }, this.watchdogMs)
  }

  private clearTimer() {
    if (this.timer) {
      clearTimeout(this.timer)
      this.timer = null
    }
  }

  isMoving() {
    return this.moving
  }
}
