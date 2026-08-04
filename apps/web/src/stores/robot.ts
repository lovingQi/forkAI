import { defineStore } from 'pinia'
import { RobotWs } from '@/api/ws'
import { getState, getMap, getParamsInfo } from '@/api/http'
import { config } from '@/config'

export interface Point {
  x: number
  y: number
}

export interface RobotSize {
  width: number
  length: number
  length_front: number
  length_rear: number
}

function parseTriple(s?: string): [number, number, number] {
  if (!s) return [0, 0, 0]
  const parts = s.split(',').map((v) => parseFloat(v))
  return [parts[0] || 0, parts[1] || 0, parts[2] || 0]
}

function parsePointsString(s?: string): Point[] {
  if (!s) return []
  const nums = s.trim().split(/\s+/).map((v) => parseFloat(v))
  const pts: Point[] = []
  for (let i = 0; i + 1 < nums.length; i += 2) {
    pts.push({ x: nums[i], y: nums[i + 1] })
  }
  return pts
}

let wsClient: RobotWs | null = null

export const useRobotStore = defineStore('robot', {
  state: () => ({
    connected: false,
    name: '',
    ip: '',
    robotType: '',
    mode: '',
    status: '',
    mapName: '',
    score: 0,
    battery: 0,
    charging: false,
    ctrlMode: 0,
    safe: true,
    motor: false,
    alarm: 'normal',
    currentRoutes: {} as Record<string, any>,
    forkInfo: {} as Record<string, any>,
    io: { input: [] as number[], output: [] as number[], virtual: [] as number[] },
    vel: [0, 0, 0] as [number, number, number],
    pose: [0, 0, 0] as [number, number, number],
    laserGroups: [] as Point[][],
    pathPoints: [] as Point[],
    clearances: [] as Point[],
    robotSize: { width: 0, length: 0, length_front: 0, length_rear: 0 } as RobotSize,
    map: { name: '', data: null as any },
    paramsInfo: {} as Record<string, Record<string, any>>,
    lastUpdate: 0
  }),

  actions: {
    applyHigh(msg: any) {
      if (msg.vel) this.vel = parseTriple(msg.vel)
      if (msg.pose) this.pose = parseTriple(msg.pose)
      if (msg.laser_data && Array.isArray(msg.laser_data.data)) {
        this.laserGroups = msg.laser_data.data.map((g: any) => parsePointsString(g.points))
      }
      if (msg.path_points && Array.isArray(msg.path_points.points)) {
        this.pathPoints = msg.path_points.points.map((p: any) => ({ x: p.x, y: p.y }))
      }
      if (msg.clearances && Array.isArray(msg.clearances.points)) {
        this.clearances = msg.clearances.points.map((p: any) => ({ x: p.x, y: p.y }))
      }
      if (msg.robot_size) {
        this.robotSize = {
          width: msg.robot_size.width || 0,
          length: msg.robot_size.length || 0,
          length_front: msg.robot_size.length_front || 0,
          length_rear: msg.robot_size.length_rear || 0
        }
      }
      this.lastUpdate = Date.now()
    },

    applyLow(msg: any) {
      if (msg.name !== undefined) this.name = msg.name
      if (msg.ip !== undefined) this.ip = msg.ip
      if (msg.robot_type !== undefined) this.robotType = msg.robot_type
      if (msg.mode !== undefined) this.mode = msg.mode
      if (msg.status !== undefined) this.status = msg.status
      if (msg.map_name !== undefined) this.mapName = msg.map_name
      if (msg.score !== undefined) this.score = msg.score
      if (msg.battery !== undefined) this.battery = msg.battery
      if (msg.charing !== undefined) this.charging = !!msg.charing
      if (msg.ctrl_mode !== undefined) this.ctrlMode = msg.ctrl_mode
      if (msg.safe !== undefined) this.safe = !!msg.safe
      if (msg.motor !== undefined) this.motor = !!msg.motor
      if (msg.alarm !== undefined) this.alarm = msg.alarm
      if (msg.current_routes !== undefined) this.currentRoutes = msg.current_routes
      if (msg.fork_info !== undefined) this.forkInfo = msg.fork_info
      if (msg.input || msg.output || msg.virtual) {
        this.io = {
          input: msg.input || this.io.input,
          output: msg.output || this.io.output,
          virtual: msg.virtual || this.io.virtual
        }
      }
    },

    applyMessage(msg: any) {
      if (!msg) return
      if (msg.type === 'high') this.applyHigh(msg)
      else if (msg.type === 'low') this.applyLow(msg)
      else {
        this.applyHigh(msg)
        this.applyLow(msg)
      }
    },

    connectWs() {
      if (wsClient) return
      wsClient = new RobotWs(
        (msg) => this.applyMessage(msg),
        (connected) => {
          this.connected = connected
        },
        config.wsBase
      )
      wsClient.connect()
    },

    disconnectWs() {
      if (wsClient) {
        wsClient.close()
        wsClient = null
      }
      this.connected = false
    },

    async loadInitial() {
      try {
        const state = await getState()
        this.applyMessage(state)
      } catch {
        /* ignore */
      }
      try {
        this.map = await getMap()
      } catch {
        /* ignore */
      }
      try {
        this.paramsInfo = await getParamsInfo()
      } catch {
        /* ignore */
      }
    }
  }
})
