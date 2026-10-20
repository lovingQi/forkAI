import { config } from '@/config'
import { getPairToken, http } from '@/api/http'

export type MappingTask = {
  id: string
  dimension: string
  mode: string
  name: string
  profileId: string
  status: string
  stopReason: string | null
  stats: Record<string, number>
  error: { code: string; message: string; diagnosticId: string } | null
  projectId: string | null
  createdAt: string
}

export async function mappingStatus() {
  const { data } = await http.get('/mapping/status', { timeout: 15000 })
  return data as {
    engines: { driver: string; dimension: string; available: boolean; reason: string; engineVersion: string }[]
    capabilities: Record<string, { online: boolean; offline: boolean; incremental: boolean }>
    activeTaskId: string | null
  }
}

export async function mappingProfiles() {
  const { data } = await http.get('/mapping/profiles')
  return data.profiles as { id: string; name: string; dimension?: string; inputs: any }[]
}

export async function mappingRobot() {
  const { data } = await http.get('/mapping/robot')
  return data as { urdfLoaded: boolean; urdfSha256: string; frames: string[] }
}

export async function validateDraft(body: Record<string, unknown>) {
  const { data } = await http.post('/mapping/drafts/validate', body, { timeout: 20000 })
  return data as {
    status: 'ready' | 'warning' | 'error'
    checks: { id: string; status: string; label: string; detail: string }[]
    issues: { code: string; message: string }[]
  }
}

export async function startMapping(body: Record<string, unknown>) {
  const { data } = await http.post('/mapping/tasks', body, { timeout: 20000 })
  return data as MappingTask
}

export async function activeMappingTask() {
  const res = await http.get('/mapping/tasks/active', { timeout: 15000, validateStatus: (s) => s === 200 || s === 204 })
  if (res.status === 204) return null
  return res.data as MappingTask
}

export async function getMappingTask(id: string) {
  const { data } = await http.get(`/mapping/tasks/${id}`)
  return data as MappingTask
}

export async function mappingActivity(id: string, after: number) {
  const { data } = await http.get(`/mapping/tasks/${id}/activity`, { params: { after } })
  return data as { events: { sequence: number; type: string; data: any }[]; latestSequence: number; reset: boolean }
}

export async function finishMapping(id: string) {
  const { data } = await http.post(`/mapping/tasks/${id}/finish`, {}, { timeout: 20000 })
  return data as MappingTask
}

export async function cancelMapping(id: string) {
  const { data } = await http.post(`/mapping/tasks/${id}/cancel`, {}, { timeout: 20000 })
  return data as MappingTask
}

export async function mappingMaps() {
  const { data } = await http.get('/mapping/maps')
  return data.maps as { projectId: string; name: string; createdAt: string; engineVersion: string; dimension?: string }[]
}

export async function deleteMappingMap(id: string) {
  await http.delete(`/mapping/maps/${id}`)
}

export function mappingPreviewUrl(id: string) {
  return `${config.apiBase}/mapping/maps/${id}/preview.png`
}

export async function downloadMappingZip(id: string, name: string) {
  const res = await http.get(`/mapping/maps/${id}/export.zip`, { responseType: 'blob', timeout: 60000 })
  const url = URL.createObjectURL(res.data)
  const link = document.createElement('a')
  link.href = url
  link.download = `${name || id}.zip`
  link.click()
  URL.revokeObjectURL(url)
}

export function openMappingLive(
  taskId: string,
  handlers: { onJson: (msg: any) => void; onMap: (buffer: ArrayBuffer) => void; onStatus: (ok: boolean) => void }
) {
  let ws: WebSocket | null = null
  let closed = false
  let timer: number | null = null

  const connect = () => {
    if (closed) return
    ws = new WebSocket(`${config.wsBase}/mapping/tasks/${taskId}/live`)
    ws.binaryType = 'arraybuffer'
    ws.onopen = () => {
      ws?.send(JSON.stringify({ type: 'auth', pairToken: getPairToken() }))
      handlers.onStatus(true)
    }
    ws.onmessage = (ev) => {
      if (typeof ev.data === 'string') {
        try {
          handlers.onJson(JSON.parse(ev.data))
        } catch {
          /* 忽略坏帧 */
        }
        return
      }
      handlers.onMap(ev.data as ArrayBuffer)
    }
    ws.onclose = () => {
      handlers.onStatus(false)
      if (!closed) timer = window.setTimeout(connect, 2000)
    }
  }
  connect()
  return () => {
    closed = true
    if (timer !== null) window.clearTimeout(timer)
    ws?.close()
  }
}
