export type IntentName =
  | 'MOVE_FWD'
  | 'MOVE_BACK'
  | 'TURN_LEFT'
  | 'TURN_RIGHT'
  | 'SPEED_UP'
  | 'SPEED_DOWN'
  | 'SPEED_SET'
  | 'STOP'
  | 'IDLE'
  | 'DOCK'
  | 'GOTO_GOAL'
  | 'QUERY_BATTERY'
  | 'QUERY_MODE'
  | 'QUERY_POSE'
  | 'QUERY_FORK_HEIGHT'
  | 'QUERY_MOTOR'
  | 'QUERY_ALARM'
  | 'UNKNOWN'

export type VoiceChannel = 'cabin' | 'ptt'

export type SpeakTarget = 'vehicle' | 'device' | 'both'

export type SpeakStyle = 'ok' | 'fail' | 'wake'

export interface ParsedIntent {
  name: IntentName
  slots: Record<string, string | number>
  rawText: string
}

export interface PairStartResponse {
  code: string
  expiresAt: number
}

export interface PairConfirmResponse {
  pairToken: string
  expiresAt: number
  clientId: string
}

export interface SiteUnlockRequest {
  nonce?: string
  code?: string
  force?: boolean
}

export interface SiteSessionInfo {
  siteSessionId: string
  clientId: string
  expiresAt: number
  holderClientId: string
}

export interface IntentResult {
  ok: boolean
  intent: ParsedIntent
  errorCode?: string
  message?: string
  utteranceKey?: string
  utteranceParams?: Record<string, string | number>
}

export type GatewayEventType =
  | 'site_changed'
  | 'watchdog_stop'
  | 'tts'
  | 'intent'
  | 'pair_required'
  | 'wake_armed'

export interface GatewayEvent {
  type: GatewayEventType
  payload: Record<string, unknown>
  ts: number
}

export const MOTION_INTENTS: IntentName[] = [
  'MOVE_FWD',
  'MOVE_BACK',
  'TURN_LEFT',
  'TURN_RIGHT',
  'SPEED_UP',
  'SPEED_DOWN',
  'SPEED_SET'
]

export const TASK_INTENTS: IntentName[] = ['IDLE', 'DOCK', 'GOTO_GOAL', 'STOP']

export const QUERY_INTENTS: IntentName[] = [
  'QUERY_BATTERY',
  'QUERY_MODE',
  'QUERY_POSE',
  'QUERY_FORK_HEIGHT',
  'QUERY_MOTOR',
  'QUERY_ALARM'
]
