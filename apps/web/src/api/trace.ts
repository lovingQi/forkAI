import { http } from '@/api/http'

export type TraceCat =
  | 'HEAR'
  | 'NLU'
  | 'PLAN'
  | 'ASK'
  | 'GUARD'
  | 'ACT'
  | 'TASK'
  | 'OBS'
  | 'SAY'
  | 'LINK'

export interface TraceMetric {
  kind: 'asr' | 'llm' | 'rules' | 'tts'
  model?: string
  engine?: string
  ms?: number | null
  ok?: boolean
  where?: 'cloud' | 'local' | 'browser'
}

export interface TraceEntry {
  id: number
  ts: number
  traceId: string | null
  cat: TraceCat | string
  msg: string
  status: 'info' | 'running' | 'ok' | 'warn' | 'fail' | string
  durMs?: number | null
  data?: { metric?: TraceMetric; [k: string]: any } | null
}

export interface MissionStep {
  id: string
  call: string
  label: string
  status: 'queued' | 'running' | 'waiting' | 'sent' | 'done' | 'failed' | 'skipped' | 'paused' | string
  inserted: boolean
  note?: string | null
}

export interface Mission {
  traceId: string
  source: 'voice' | 'flow' | string
  title: string
  status: 'running' | 'waiting' | 'paused' | 'succeeded' | 'failed' | 'cancelled' | string
  startedTs: number
  endedTs: number | null
  steps: MissionStep[]
}

export const CATS: { id: TraceCat; zh: string; en: string; color: string }[] = [
  { id: 'HEAR', zh: '听到', en: 'Speech heard', color: '#39c5ff' },
  { id: 'NLU', zh: '理解', en: 'Language understanding', color: '#b48cff' },
  { id: 'PLAN', zh: '规划', en: 'Task planning', color: '#b6ff3b' },
  { id: 'ASK', zh: '追问', en: 'Clarification', color: '#ffc53d' },
  { id: 'GUARD', zh: '安全', en: 'Safety guard', color: '#ff4d4f' },
  { id: 'ACT', zh: '下发', en: 'Command dispatch', color: '#d4ff7a' },
  { id: 'TASK', zh: '执行', en: 'Task execution', color: '#7ee787' },
  { id: 'OBS', zh: '观察', en: 'Observation', color: '#ffe066' },
  { id: 'SAY', zh: '播报', en: 'Voice reply', color: '#5eead4' },
  { id: 'LINK', zh: '连接', en: 'Link status', color: '#ff9f43' }
]

export function catMeta(cat: string) {
  return CATS.find((c) => c.id === cat) || { id: cat, zh: cat, en: cat, color: '#7f8f84' }
}

export async function getTrace(limit = 300): Promise<{ entries: TraceEntry[]; mission: Mission | null }> {
  const { data } = await http.get('/trace', { params: { limit } })
  return { entries: data.entries || [], mission: data.mission || null }
}
