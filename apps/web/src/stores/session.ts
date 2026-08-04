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

/** 播放 base64 音频（piper wav）。autoplay 被拦截或出错时回退浏览器 TTS。 */
function playBase64Audio(audioBase64: string, text: string, style: string) {
  try {
    const audio = new Audio(`data:audio/wav;base64,${audioBase64}`)
    const p = audio.play()
    if (p && typeof p.catch === 'function') {
      p.then(() => console.log('[forkai] 走 piper 音频播放'))
        .catch((e) => {
          console.warn('[forkai] piper 音频被拦截，回退 speechSynthesis:', e?.name || e)
          speakBrowser(text, style)
        })
    } else {
      console.log('[forkai] 走 piper 音频播放')
    }
  } catch (e) {
    console.warn('[forkai] piper 音频构造失败，回退 speechSynthesis:', e)
    speakBrowser(text, style)
  }
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
    lastIntent: '',
    listening: false,
    wakeArmed: false,
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
        this.pushLog(`TTS: ${text}`)
        const target = msg.payload?.target || 'device'
        if (target === 'device' || target === 'both') {
          const audioBase64 = msg.payload?.audioBase64
          const style = msg.payload?.style || 'ok'
          console.log('[forkai] tts 事件: text=%s audioBase64长度=%d', text, (audioBase64 || '').length)
          // 有真实音频（piper）优先播放；否则回退浏览器 speechSynthesis
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
      if (msg.type === 'wake_armed') {
        this.wakeArmed = true
        setTimeout(() => {
          this.wakeArmed = false
        }, 12000)
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
