import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { SpeakStyle, SpeakTarget, VoiceChannel } from '@forkai/shared'
import type { GatewayConfig } from './config.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

export class SpeakService {
  private templates: Record<string, string>

  constructor(private cfg: GatewayConfig) {
    const p = path.resolve(__dirname, '../config/utterances.zh-CN.json')
    this.templates = JSON.parse(fs.readFileSync(p, 'utf8'))
  }

  render(key: string, params: Record<string, string | number> = {}): string {
    let text = this.templates[key] || key
    for (const [k, v] of Object.entries(params)) {
      text = text.replace(new RegExp(`\\{${k}\\}`, 'g'), String(v))
    }
    return text
  }

  resolveTarget(
    channel: VoiceChannel,
    kind: 'move' | 'query' | 'wake' | 'fail'
  ): SpeakTarget {
    if (kind === 'wake') return 'vehicle'
    if (kind === 'query') return this.cfg.speak.query
    if (channel === 'cabin') return this.cfg.speak.cabinMove
    return this.cfg.speak.pttMove
  }

  async speak(opts: {
    text: string
    style: SpeakStyle
    target: SpeakTarget
    clientId?: string
  }): Promise<{ text: string; audioBase64?: string }> {
    try {
      const res = await fetch(`${this.cfg.speech.baseUrl.replace(/\/$/, '')}/tts/speak`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: opts.text,
          style: opts.style,
          target: opts.target
        })
      })
      if (!res.ok) return { text: opts.text }
      const data = (await res.json()) as { audioBase64?: string }
      return { text: opts.text, audioBase64: data.audioBase64 }
    } catch {
      return { text: opts.text }
    }
  }
}
