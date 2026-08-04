import type { GatewayConfig } from './config.js'

export class JarvisClient {
  constructor(private cfg: GatewayConfig) {}

  private url(p: string) {
    return `${this.cfg.jarvis.baseUrl.replace(/\/$/, '')}${p}`
  }

  async getState(): Promise<any> {
    const res = await fetch(this.url('/api/state'))
    if (!res.ok) throw new Error(`jarvis state ${res.status}`)
    return res.json()
  }

  async getMap(): Promise<any> {
    const res = await fetch(this.url('/api/map'))
    if (!res.ok) throw new Error(`jarvis map ${res.status}`)
    return res.json()
  }

  async getParams(): Promise<any> {
    const res = await fetch(this.url('/api/params'))
    if (!res.ok) throw new Error(`jarvis params ${res.status}`)
    return res.json()
  }

  async control(action: string, payload: Record<string, unknown> = {}): Promise<any> {
    const res = await fetch(this.url(`/api/control/${action}`), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
    const data = await res.json().catch(() => ({}))
    return data
  }
}
