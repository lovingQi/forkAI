import { http } from '@/api/http'

export type ConsoleStatus = {
  estopLatched: boolean
  avoidEnabled: boolean
  forkBusy: boolean
  chassisMoving: boolean
  allowRemoteForce: boolean
  limits: {
    trans: { min: number; max: number; step: number; default: number; unsafeMax: number }
    rot: number
    forkLift: { min: number; max: number; step: number; confirmThreshold: number }
    forkExtend: { min: number; max: number; step: number; tolerance: number }
  }
}

export type SelfCheck = {
  ok: boolean
  core: { ok: boolean; detail?: string }
  jarvis: { ok: boolean; latencyMs?: number; error?: string }
  rosbridge: { ok: boolean; status?: string; detail?: string }
}

const ERROR_TEXT: Record<string, string> = {
  no_site: '没有控制权',
  not_local: '只有车载屏可以执行',
  estop: '急停中',
  fork_busy: '货叉动作未完成',
  chassis_moving: '车体还在动',
  robot_moving: '车辆未停稳',
  flow_running: '任务流执行中',
  confirm_required: '需要确认',
  not_supported: '当前车辆不支持',
  invalid_direction: '方向无效',
  invalid_params: '参数无效',
  invalid_kiosk: '车载屏口令无效',
  invalid_ticket: '二维码已失效',
  use_console: '请走控制台接口'
}

export function consoleErrorMessage(e: any): string {
  const code = e?.response?.data?.error
  return ERROR_TEXT[code] || e?.response?.data?.error || e?.message || '操作失败'
}

export async function getSelfCheck(): Promise<SelfCheck> {
  const { data } = await http.get('/console/selfcheck')
  return data
}

export async function pairLocal(kioskToken: string) {
  const { data } = await http.post('/pair/local', { kioskToken })
  return data as {
    succeed: boolean
    pairToken: string
    clientId: string
    expiresAt: number
    vehicleId: string
    local: boolean
  }
}

export async function joinByTicket(ticket: string) {
  const { data } = await http.post('/pair/join', { ticket })
  return data as {
    succeed: boolean
    pairToken: string
    clientId: string
    expiresAt: number
    vehicleId: string
    local: boolean
    siteSessionId: string
    siteExpiresAt: number
    holderClientId: string
  }
}

export async function getJoinTicket() {
  const { data } = await http.get('/pair/join-ticket')
  return data as { ticket: string; expiresAt: number; url: string }
}

export async function getSetup() {
  const { data } = await http.get('/console/setup')
  return data as { completed: boolean; completedAt?: number }
}

export async function completeSetup() {
  const { data } = await http.post('/console/setup/complete')
  return data
}

export async function resetSetup() {
  const { data } = await http.post('/console/setup/reset')
  return data
}

export async function getConsoleStatus(): Promise<ConsoleStatus> {
  const { data } = await http.get('/console/status')
  return data
}

export async function consoleDrive(body: { direction: string; trans: number; start?: boolean }) {
  const { data } = await http.post('/console/drive', body)
  return data
}

export async function consoleDriveStop() {
  const { data } = await http.post('/console/drive/stop')
  return data
}

export async function consoleAvoid(enabled: boolean, confirm = false) {
  const { data } = await http.post('/console/avoid', { enabled, confirm })
  return data
}

export async function consoleFork(body: {
  axis: 'lift' | 'extend'
  mode: 'step' | 'target'
  delta?: number
  pos?: number
  confirm?: boolean
}) {
  const { data } = await http.post('/console/fork', body)
  return data
}

export async function consoleStop() {
  const { data } = await http.post('/console/stop')
  return data
}

export async function consoleEstop() {
  const { data } = await http.post('/console/estop')
  return data
}

export async function consoleEstopRelease(confirm: boolean) {
  const { data } = await http.post('/console/estop/release', { confirm })
  return data
}

export type ParamItem = {
  name?: string
  name_zh?: string
  desc?: string
  desc_zh?: string
  type?: string
  ranges?: number[]
  default?: any
  editable?: boolean
}

export type ParamSection = {
  editable: boolean
  items: Record<string, ParamItem>
}

export async function getConsoleParams() {
  const { data } = await http.get('/console/params')
  return data as { succeed: boolean; sections: Record<string, ParamSection> }
}

export async function applyConsoleParams(changes: Record<string, Record<string, any>>, confirm: boolean) {
  const { data } = await http.post('/console/params', { changes, confirm })
  return data
}

export async function getParamBackups() {
  const { data } = await http.get('/console/params/backups')
  return data as { backups: { id: string; ts: number; clientId: string }[] }
}

export async function restoreParamBackup(backupId: string, confirm: boolean) {
  const { data } = await http.post('/console/params/restore', { backupId, confirm })
  return data
}

export async function getParamLog() {
  const { data } = await http.get('/console/params/log')
  return data as { log: Record<string, any>[] }
}
