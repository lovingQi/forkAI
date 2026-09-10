import { defineStore } from 'pinia'
import {
  clearPairSession,
  confirmPair,
  endSite,
  getClientId,
  getPairPending,
  getPairToken,
  getSite,
  setPairSession,
  startPair,
  unlockSite,
  voiceText
} from '@/api/http'
import { EventWs } from '@/api/ws'

function ttsEngineLabel(engine: string, hasAudio: boolean): string {
  if (!hasAudio || engine === 'mock') return '浏览器朗读'
  if (engine === 'cloud') return '云端 CosyVoice'
  if (engine === 'cloud-cache') return '云端缓存'
  if (engine === 'piper') return '本地 piper'
  return engine || '未知'
}

function speakBrowser(text: string, style: string) {
  if (!('speechSynthesis' in window)) return
  const u = new SpeechSynthesisUtterance(text)
  u.lang = 'zh-CN'
  u.rate = style === 'fail' ? 0.9 : style === 'wake' ? 1.1 : 1.05
  const voices = window.speechSynthesis.getVoices()
  const female = voices.find((v) => /female|Xiaoxiao|Tingting|Yaoyao|中文/.test(v.name))
  if (female) u.voice = female
  window.speechSynthesis.cancel()
  window.speechSynthesis.speak(u)
}

/** 常驻 AudioContext 播放通道：每次 new Audio 都会重开输出流，设备休眠时
 *  开头 1~2 字会被流建立延迟吃掉；首次播放创建并复用同一 AudioContext 保持链路热备，
 *  配合服务端前导静音垫覆盖硬件唤醒期。失败回退浏览器 speechSynthesis。 */
let audioCtx: AudioContext | null = null
let currentSrc: AudioBufferSourceNode | null = null

function ensureAudioCtx(): AudioContext {
  if (!audioCtx) audioCtx = new AudioContext()
  if (audioCtx.state === 'suspended') void audioCtx.resume()
  return audioCtx
}

function base64ToArrayBuffer(b64: string): ArrayBuffer {
  const bin = atob(b64)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  return bytes.buffer
}

async function playBase64Audio(audioBase64: string, text: string, style: string) {
  try {
    const ctx = ensureAudioCtx()
    const buf = await ctx.decodeAudioData(base64ToArrayBuffer(audioBase64))
    stopCurrentAudio()
    const src = ctx.createBufferSource()
    src.buffer = buf
    src.connect(ctx.destination)
    src.onended = () => {
      if (currentSrc === src) currentSrc = null
    }
    currentSrc = src
    src.start()
    console.log('[forkai] TTS 音频播放(AudioContext)')
  } catch (e) {
    console.warn('[forkai] TTS 音频播放失败，回退 speechSynthesis:', e)
    speakBrowser(text, style)
  }
}

/** TTS 打断：停止当前 piper 音频与浏览器朗读（收到 asr_final 时调用）。 */
function stopCurrentAudio() {
  if (currentSrc) {
    try {
      currentSrc.stop()
    } catch {
      /* 已停止的 source 重复 stop 会抛 InvalidStateError，忽略 */
    }
    currentSrc = null
  }
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
}

let eventWs: EventWs | null = null

export const useSessionStore = defineStore('session', {
  state: () => ({
    paired: !!getPairToken(),
    clientId: getClientId(),
    vehicleId: '',
    pairCode: '' as string,
    siteCode: '' as string,
    siteNonce: '' as string,
    siteExpiresAt: 0,
    siteHolderId: '' as string,
    iAmHolder: false,
    lastUtterance: '',
    lastTtsEngine: '',
    lastIntent: '',
    listening: false,
    wakeArmed: false,
    flowStatus: 'idle',
    flowId: '' as string,
    flowNodeStates: {} as Record<string, string>,
    logs: [] as string[]
  }),

  actions: {
    pushLog(line: string) {
      this.logs.unshift(`${new Date().toLocaleTimeString()} ${line}`)
      if (this.logs.length > 40) this.logs.pop()
    },

    connectEvents() {
      if (eventWs) return
      eventWs = new EventWs((msg) => this.onEvent(msg))
      eventWs.connect(getPairToken())
    },

    onEvent(msg: any) {
      if (!msg || !msg.type) return
      if (msg.type === 'tts') {
        const text = msg.payload?.text || ''
        this.lastUtterance = text
        const audioBase64 = msg.payload?.audioBase64
        const engineLabel = ttsEngineLabel(String(msg.payload?.ttsEngine || ''), !!audioBase64)
        this.lastTtsEngine = engineLabel
        this.pushLog(`TTS[${engineLabel}]: ${text}`)
        const target = msg.payload?.target || 'device'
        if (target === 'device' || target === 'both') {
          const style = msg.payload?.style || 'ok'
          console.log(
            '[forkai] tts 事件: engine=%s text=%s audioBase64长度=%d',
            engineLabel,
            text,
            (audioBase64 || '').length
          )
          // 有真实音频优先播放；否则回退浏览器 speechSynthesis
          if (audioBase64) {
            playBase64Audio(audioBase64, text, style)
          } else {
            console.log('[forkai] 无 audioBase64，走 speechSynthesis')
            speakBrowser(text, style)
          }
        }
      }
      if (msg.type === 'intent') {
        this.lastIntent = msg.payload?.intent?.name || ''
        this.pushLog(`意图: ${this.lastIntent} ${msg.payload?.ok ? 'OK' : 'FAIL'}`)
      }
      if (msg.type === 'site_changed') {
        this.siteHolderId = msg.payload?.holderClientId || ''
        this.siteExpiresAt = msg.payload?.expiresAt || 0
        this.iAmHolder = this.siteHolderId === this.clientId
        this.pushLog(this.iAmHolder ? '已获得现场点动权' : '现场点动权变更')
      }
      if (msg.type === 'watchdog_stop') {
        this.pushLog('看门狗停车')
      }
      if (msg.type === 'asr_final') {
        // TTS 打断：识别出 final 指令时停播当前音频
        stopCurrentAudio()
        this.pushLog(`识别: ${msg.payload?.text || ''}`)
      }
      if (msg.type === 'wake_armed') {
        this.wakeArmed = true
        setTimeout(() => {
          this.wakeArmed = false
        }, 12000)
      }
      if (msg.type === 'flow_event') {
        const p = msg.payload || {}
        if (p.flowStatus) this.flowStatus = p.flowStatus
        if (p.flowId) this.flowId = p.flowId
        if (p.nodeId && p.nodeStatus) {
          this.flowNodeStates = { ...this.flowNodeStates, [p.nodeId]: p.nodeStatus }
        }
      }
    },

    async refreshPairCode() {
      const pending = await getPairPending()
      if (pending.code) {
        this.pairCode = pending.code
        return
      }
      const started = await startPair()
      this.pairCode = started.code
    },

    async doConfirmPair(code: string) {
      const res = await confirmPair(code)
      setPairSession(res.pairToken, res.clientId)
      this.paired = true
      this.clientId = res.clientId
      this.vehicleId = res.vehicleId
      this.connectEvents()
      await this.refreshSite()
      return res
    },

    logout() {
      clearPairSession()
      this.paired = false
      this.clientId = ''
      this.iAmHolder = false
      if (eventWs) {
        eventWs.close()
        eventWs = null
      }
    },

    async refreshSite() {
      if (!this.paired) return
      const site = await getSite()
      this.vehicleId = site.vehicleId
      this.siteCode = site.code
      this.siteNonce = site.nonce
      if (site.session) {
        this.siteExpiresAt = site.session.expiresAt
        this.siteHolderId = site.session.holderClientId
        this.iAmHolder = site.session.holderClientId === this.clientId
      } else {
        this.siteExpiresAt = 0
        this.siteHolderId = ''
        this.iAmHolder = false
      }
    },

    async doUnlock(opts: { code?: string; nonce?: string; force?: boolean }) {
      try {
        await unlockSite(opts)
        await this.refreshSite()
        return true
      } catch (e: any) {
        if (e?.response?.status === 409) {
          return 'held'
        }
        throw e
      }
    },

    async doEndSite() {
      await endSite(false)
      await this.refreshSite()
    },

    async sendText(text: string, channel: 'cabin' | 'ptt') {
      this.listening = false
      const res = await voiceText(text, channel)
      if (res.utterance) this.lastUtterance = res.utterance
      if (res.intent?.name) this.lastIntent = res.intent.name
      this.pushLog(`我说: ${text}`)
      return res
    },

    applyQueryParams() {
      const q = new URLSearchParams(window.location.search)
      const site = q.get('site')
      const v = q.get('v')
      if (v) this.vehicleId = v
      return { siteNonce: site || '' }
    }
  }
})
