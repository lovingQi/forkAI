import { config } from '@/config'

type MessageHandler = (msg: any) => void
type StatusHandler = (connected: boolean) => void

class WsChannel {
  private url: string
  private ws: WebSocket | null = null
  private onMessage: MessageHandler
  private onStatus?: StatusHandler
  private closedByUser = false
  private retryTimer: number | null = null

  constructor(url: string, onMessage: MessageHandler, onStatus?: StatusHandler) {
    this.url = url
    this.onMessage = onMessage
    this.onStatus = onStatus
  }

  connect() {
    this.closedByUser = false
    this.open()
  }

  private open() {
    try {
      this.ws = new WebSocket(this.url)
    } catch {
      this.scheduleReconnect()
      return
    }
    this.ws.onopen = () => this.onStatus && this.onStatus(true)
    this.ws.onmessage = (ev) => {
      try {
        this.onMessage(JSON.parse(ev.data))
      } catch {
        /* ignore */
      }
    }
    this.ws.onclose = () => {
      this.onStatus && this.onStatus(false)
      if (!this.closedByUser) this.scheduleReconnect()
    }
    this.ws.onerror = () => this.ws && this.ws.close()
  }

  private scheduleReconnect() {
    if (this.retryTimer !== null) return
    this.retryTimer = window.setTimeout(() => {
      this.retryTimer = null
      if (!this.closedByUser) this.open()
    }, 2000)
  }

  close() {
    this.closedByUser = true
    if (this.retryTimer !== null) {
      clearTimeout(this.retryTimer)
      this.retryTimer = null
    }
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
  }

  send(obj: unknown) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(obj))
    }
  }
}

export class RobotWs {
  private high: WsChannel
  private low: WsChannel

  constructor(onMessage: MessageHandler, onStatus?: StatusHandler, wsBase = config.wsBase) {
    this.high = new WsChannel(`${wsBase}/high`, onMessage, onStatus)
    this.low = new WsChannel(`${wsBase}/low`, onMessage)
  }

  connect() {
    this.high.connect()
    this.low.connect()
  }

  close() {
    this.high.close()
    this.low.close()
  }
}

export class EventWs {
  private ch: WsChannel

  constructor(onMessage: MessageHandler, wsBase = config.wsBase) {
    this.ch = new WsChannel(`${wsBase}/events`, onMessage)
  }

  connect(pairToken?: string) {
    this.ch.connect()
    if (pairToken) {
      setTimeout(() => this.ch.send({ type: 'auth', pairToken }), 300)
    }
  }

  close() {
    this.ch.close()
  }
}
