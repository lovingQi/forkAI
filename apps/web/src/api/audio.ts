/**
 * 麦克风采集 + /ws/audio 客户端。
 * MicCapture：getUserMedia → AudioContext + AudioWorklet（ScriptProcessor 兜底）
 *   → 重采样到 16kHz PCM16 → 回调 ArrayBuffer。
 * AudioWs：连 /ws/audio，首帧发 {pairToken, channel}，之后发二进制 PCM；
 *   收 {"type":"partial"|"final"|"error"}。
 */
import { config } from '@/config'

export type PcmHandler = (pcm: ArrayBuffer) => void

const TARGET_RATE = 16000

// 内联 worklet：把原始 float32 帧转交主线程（避免额外构建配置）
const WORKLET_CODE = `
class PcmTapProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0]
    if (ch && ch.length) this.port.postMessage(ch.slice(0))
    return true
  }
}
registerProcessor('pcm-tap', PcmTapProcessor)
`

export class MicCapture {
  private ctx: AudioContext | null = null
  private stream: MediaStream | null = null
  private node: AudioWorkletNode | ScriptProcessorNode | null = null
  private pending: number[] = []
  private srcRate = TARGET_RATE

  async start(onPcm: PcmHandler): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true }
    })
    this.ctx = new AudioContext()
    this.srcRate = this.ctx.sampleRate
    const source = this.ctx.createMediaStreamSource(this.stream)
    const emitChunk = (floats: Float32Array) => {
      for (let i = 0; i < floats.length; i++) this.pending.push(floats[i])
      // 凑够约 100ms 源采样再重采样输出
      const block = Math.floor(this.srcRate * 0.1)
      while (this.pending.length >= block) {
        const seg = this.pending.splice(0, block)
        onPcm(this.toPcm16(seg))
      }
    }
    if (this.ctx.audioWorklet) {
      try {
        const url = URL.createObjectURL(
          new Blob([WORKLET_CODE], { type: 'application/javascript' })
        )
        await this.ctx.audioWorklet.addModule(url)
        URL.revokeObjectURL(url)
        const node = new AudioWorkletNode(this.ctx, 'pcm-tap')
        node.port.onmessage = (e) => emitChunk(e.data as Float32Array)
        source.connect(node)
        node.connect(this.ctx.destination)
        this.node = node
        return
      } catch {
        /* 回落 ScriptProcessor */
      }
    }
    const sp = this.ctx.createScriptProcessor(4096, 1, 1)
    sp.onaudioprocess = (e) => emitChunk(e.inputBuffer.getChannelData(0))
    source.connect(sp)
    sp.connect(this.ctx.destination)
    this.node = sp
  }

  /** 线性插值重采样到 16kHz，转 PCM16 little-endian。 */
  private toPcm16(seg: number[]): ArrayBuffer {
    const ratio = this.srcRate / TARGET_RATE
    const nOut = Math.floor(seg.length / ratio)
    const out = new Int16Array(nOut)
    for (let i = 0; i < nOut; i++) {
      const pos = i * ratio
      const i0 = Math.floor(pos)
      const i1 = Math.min(i0 + 1, seg.length - 1)
      const frac = pos - i0
      let v = seg[i0] * (1 - frac) + seg[i1] * frac
      v = Math.max(-1, Math.min(1, v))
      out[i] = v < 0 ? v * 0x8000 : v * 0x7fff
    }
    return out.buffer
  }

  stop() {
    if (this.node) {
      this.node.disconnect()
      this.node = null
    }
    if (this.stream) {
      this.stream.getTracks().forEach((t) => t.stop())
      this.stream = null
    }
    if (this.ctx) {
      this.ctx.close().catch(() => {})
      this.ctx = null
    }
    this.pending = []
  }
}

export interface AudioFinalMessage {
  type: 'final'
  text: string
  succeed?: boolean
  intent?: { name: string }
  errorCode?: string
  utterance?: string
  audioBase64?: string
  target?: string
}

export class AudioWs {
  private ws: WebSocket | null = null

  constructor(
    private onPartial: (text: string) => void,
    private onFinal: (msg: AudioFinalMessage) => void,
    private onClosed?: () => void
  ) {}

  connect(pairToken: string, channel: 'ptt' | 'cabin'): Promise<void> {
    return new Promise((resolve, reject) => {
      let settled = false
      try {
        this.ws = new WebSocket(`${config.wsBase}/audio`)
      } catch (e) {
        reject(e)
        return
      }
      this.ws.onopen = () => {
        this.ws!.send(JSON.stringify({ pairToken, channel }))
        settled = true
        resolve()
      }
      this.ws.onerror = () => {
        if (!settled) {
          settled = true
          reject(new Error('ws audio connect failed'))
        }
      }
      this.ws.onclose = (ev) => {
        if (!settled) {
          settled = true
          reject(new Error(`ws audio closed ${ev.code}`))
          return
        }
        this.onClosed && this.onClosed()
      }
      this.ws.onmessage = (ev) => {
        let msg: any
        try {
          msg = JSON.parse(ev.data)
        } catch {
          return
        }
        if (msg.type === 'partial') this.onPartial(String(msg.text || ''))
        else if (msg.type === 'final') this.onFinal(msg as AudioFinalMessage)
        else if (msg.type === 'error') console.warn('[forkai] /ws/audio error:', msg.error)
      }
    })
  }

  sendPcm(pcm: ArrayBuffer) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(pcm)
  }

  end() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ event: 'end' }))
    }
  }

  close() {
    if (this.ws) {
      this.ws.onclose = null
      this.ws.close()
      this.ws = null
    }
  }
}
