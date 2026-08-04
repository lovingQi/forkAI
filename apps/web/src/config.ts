export interface AppConfig {
  apiBase: string
  wsBase: string
}

function resolveWsBase(raw?: string): string {
  if (!raw) raw = '/ws'
  if (raw.startsWith('ws://') || raw.startsWith('wss://')) return raw
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const path = raw.startsWith('/') ? raw : '/' + raw
  return `${proto}//${window.location.host}${path}`
}

const injected = window.__APP_CONFIG__ || {}

export const config: AppConfig = {
  apiBase: injected.apiBase || '/api',
  wsBase: resolveWsBase(injected.wsBase)
}
