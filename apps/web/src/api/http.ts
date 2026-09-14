import axios from 'axios'
import { config } from '@/config'

const TOKEN_KEY = 'forkai_pair_token'
const CLIENT_KEY = 'forkai_client_id'

export function getPairToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setPairSession(token: string, clientId: string) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(CLIENT_KEY, clientId)
}

export function clearPairSession() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(CLIENT_KEY)
}

export function getClientId() {
  return localStorage.getItem(CLIENT_KEY) || ''
}

export const http = axios.create({
  baseURL: config.apiBase,
  timeout: 10000
})

http.interceptors.request.use((cfg) => {
  const token = getPairToken()
  if (token) {
    cfg.headers = cfg.headers || {}
    cfg.headers.Authorization = `Bearer ${token}`
  }
  return cfg
})

http.interceptors.response.use(
  (res) => res,
  (err) => {
    const status = err?.response?.status
    const code = err?.response?.data?.error
    if (status === 401 && code === 'unpaired') {
      clearPairSession()
      window.dispatchEvent(new Event('forkai-unpaired'))
    }
    return Promise.reject(err)
  }
)

export async function getState(): Promise<any> {
  const { data } = await http.get('/state')
  return data
}

export async function getMap(): Promise<{ name: string; data: any }> {
  const { data } = await http.get('/map')
  return data
}

export async function getParamsInfo(): Promise<any> {
  const { data } = await http.get('/params')
  return data && data.params ? data.params : {}
}

export async function control(action: string, payload: Record<string, any> = {}) {
  const { data } = await http.post(`/control/${action}`, payload)
  return data
}

export async function startPair() {
  const { data } = await http.post('/pair/start')
  return data as { code: string; expiresAt: number }
}

export async function getPairPending() {
  const { data } = await http.get('/pair/pending')
  return data as { code: string | null; expiresAt: number }
}

export async function confirmPair(code: string) {
  const { data } = await http.post('/pair/confirm', { code })
  return data as {
    succeed: boolean
    pairToken: string
    clientId: string
    expiresAt: number
    vehicleId: string
  }
}

export async function getSite() {
  const { data } = await http.get('/site')
  return data as {
    vehicleId: string
    nonce: string
    code: string
    session: null | { siteSessionId: string; holderClientId: string; expiresAt: number }
  }
}

export async function unlockSite(body: { nonce?: string; code?: string; force?: boolean }) {
  const { data } = await http.post('/site/unlock', body)
  return data
}

export async function endSite(force = false) {
  const { data } = await http.post('/site/end', { force })
  return data
}

export async function voiceText(text: string, channel: 'cabin' | 'ptt') {
  const { data } = await http.post('/voice/text', { text, channel }, { timeout: 40000 })
  return data as {
    succeed: boolean
    utterance?: string
    audioBase64?: string
    target?: string
    intent?: { name: string; rawText: string }
    errorCode?: string
  }
}

export async function voiceStop() {
  const { data } = await http.post('/voice/stop')
  return data
}

export async function getHealth() {
  const { data } = await http.get('/health')
  return data
}

export type VoiceProviderItem = {
  id: string
  name: string
  latencyMs: number | null
  error: string | null
}

export async function getVoiceProviders(probe = false, signal?: AbortSignal) {
  const { data } = await http.get('/voice/providers', {
    params: probe ? { probe: 1 } : {},
    timeout: probe ? 30000 : 15000,
    signal
  })
  return data as {
    asr: { selected: string; items: VoiceProviderItem[] }
    tts: { selected: string; items: VoiceProviderItem[] }
    ttsVoice: { selected: string; items: VoiceProviderItem[] }
    llm: { selected: string; items: VoiceProviderItem[] }
    nluRulesEnabled: boolean
    nluMaxIntents: number
    llmThinkingEnabled: boolean
  }
}

export async function setVoiceProviders(body: {
  asrModel?: string
  ttsModel?: string
  ttsVoice?: string
  llmModel?: string
  nluRulesEnabled?: boolean
  nluMaxIntents?: number
  llmThinkingEnabled?: boolean
}) {
  const { data } = await http.put('/voice/providers', body)
  return data as {
    succeed: boolean
    asrModel: string
    ttsModel: string
    ttsVoice: string
    llmModel: string
    nluRulesEnabled: boolean
    nluMaxIntents: number
    llmThinkingEnabled: boolean
  }
}
