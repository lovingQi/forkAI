import { defineStore } from 'pinia'
import {
  getConsoleStatus,
  getSetup,
  type ConsoleStatus,
  type SelfCheck
} from '@/api/console'

export const useConsoleStore = defineStore('console', {
  state: () => ({
    booted: false,
    isKiosk: localStorage.getItem('forkai_is_kiosk') === '1',
    kioskToken: localStorage.getItem('forkai_kiosk_token') || '',
    setupCompleted: null as boolean | null,
    selfcheck: null as SelfCheck | null,
    status: null as ConsoleStatus | null,
    joinNotice: '',
    joinSerial: 0
  }),
  actions: {
    setKiosk(token: string) {
      this.isKiosk = true
      this.kioskToken = token
      localStorage.setItem('forkai_is_kiosk', '1')
      localStorage.setItem('forkai_kiosk_token', token)
    },
    clearKiosk() {
      this.isKiosk = false
      this.kioskToken = ''
      localStorage.removeItem('forkai_is_kiosk')
      localStorage.removeItem('forkai_kiosk_token')
    },
    finishBoot() {
      this.booted = true
    },
    async loadSetup() {
      const setup = await getSetup()
      this.setupCompleted = !!setup.completed
      return setup
    },
    async loadStatus() {
      this.status = await getConsoleStatus()
      return this.status
    },
    onEvent(msg: any) {
      if (!msg || !msg.type) return
      if (msg.type === 'console_state' && msg.payload) {
        this.status = { ...(this.status || {}), ...msg.payload } as ConsoleStatus
      }
      if (msg.type === 'console_estop') {
        if (this.status) this.status = { ...this.status, estopLatched: true }
        void this.loadStatus().catch(() => {})
      }
      if (msg.type === 'join_consumed') {
        this.joinSerial += 1
        this.joinNotice = '手机已接入'
      }
      if (msg.type === 'setup_changed') {
        this.setupCompleted = !!msg.payload?.completed
      }
    }
  }
})
