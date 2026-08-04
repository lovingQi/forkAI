import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import express from 'express'
import cors from 'cors'
import { WebSocketServer, WebSocket } from 'ws'
import type { VoiceChannel } from '@forkai/shared'
import { loadConfig } from './config.js'
import { JarvisClient } from './jarvis.js'
import { SessionManager } from './session.js'
import { MotionWatchdog } from './watchdog.js'
import { SpeakService } from './speak.js'
import { IntentExecutor } from './executor.js'
import { matchWakeWord, parseIntent } from './intent.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const cfg = loadConfig()
const jarvis = new JarvisClient(cfg)
const sessions = new SessionManager(cfg)
const speakSvc = new SpeakService(cfg)

type ClientSock = { ws: WebSocket; clientId?: string }

const eventClients = new Set<ClientSock>()

function broadcast(type: string, payload: Record<string, unknown>) {
  const msg = JSON.stringify({ type, payload, ts: Date.now() })
  for (const c of eventClients) {
    if (c.ws.readyState === WebSocket.OPEN) c.ws.send(msg)
  }
}

const watchdog = new MotionWatchdog(
  jarvis,
  cfg.watchdogMs,
  cfg.speed.max,
  cfg.speed.default,
  () => broadcast('watchdog_stop', {})
)
const executor = new IntentExecutor(cfg, sessions, jarvis, watchdog)

const app = express()
app.use(cors())
app.use(express.json({ limit: '2mb' }))

function auth(
  req: express.Request,
  res: express.Response,
  next: express.NextFunction
) {
  const header = req.header('authorization') || ''
  const token = header.startsWith('Bearer ') ? header.slice(7) : ''
  const rec = sessions.resolveToken(token)
  if (!rec) {
    res.status(401).json({ succeed: false, error: 'unpaired' })
    return
  }
  ;(req as any).clientId = rec.clientId
  ;(req as any).pairToken = token
  next()
}

app.get('/api/health', (_req, res) => {
  res.json({
    ok: true,
    vehicleId: cfg.vehicleId,
    watchdogMs: cfg.watchdogMs
  })
})

app.post('/api/pair/start', (_req, res) => {
  const pending = sessions.startPair()
  res.json({ code: pending.code, expiresAt: pending.expiresAt })
})

app.get('/api/pair/pending', (_req, res) => {
  const pending = sessions.getPairPending()
  res.json(pending || { code: null, expiresAt: 0 })
})

app.post('/api/pair/confirm', (req, res) => {
  const code = String(req.body?.code || '')
  const result = sessions.confirmPair(code)
  if (!result) {
    res.status(400).json({ succeed: false, error: 'invalid_code' })
    return
  }
  res.json({
    succeed: true,
    pairToken: result.pairToken,
    expiresAt: result.expiresAt,
    clientId: result.clientId,
    vehicleId: cfg.vehicleId
  })
})

app.get('/api/site', auth, (_req, res) => {
  res.json(sessions.getSitePublic())
})

app.post('/api/site/unlock', auth, (req, res) => {
  const clientId = (req as any).clientId as string
  const result = sessions.unlockSite(clientId, {
    nonce: req.body?.nonce,
    code: req.body?.code,
    force: !!req.body?.force
  })
  if (!result.ok) {
    if (result.error === 'held') {
      res.status(409).json({
        succeed: false,
        error: 'held',
        holderClientId: result.holderClientId
      })
      return
    }
    res.status(400).json({ succeed: false, error: 'invalid' })
    return
  }
  broadcast('site_changed', {
    holderClientId: result.session.holderClientId,
    expiresAt: result.session.expiresAt,
    stolen: !!result.stolen
  })
  res.json({
    succeed: true,
    siteSessionId: result.session.siteSessionId,
    expiresAt: result.session.expiresAt,
    holderClientId: result.session.holderClientId
  })
})

app.post('/api/site/end', auth, (req, res) => {
  const clientId = (req as any).clientId as string
  const ok = sessions.endSite(clientId, !!req.body?.force)
  if (!ok) {
    res.status(403).json({ succeed: false, error: 'not_holder' })
    return
  }
  broadcast('site_changed', { holderClientId: null })
  res.json({ succeed: true })
})

async function runUtterance(
  clientId: string,
  channel: VoiceChannel,
  text: string
) {
  let wakeOk = false
  if (channel === 'cabin' && matchWakeWord(text, cfg.wakeWords)) {
    sessions.armWake(clientId)
    wakeOk = true
    const wakeText = speakSvc.render('wake_ack')
    const spoken = await speakSvc.speak({
      text: wakeText,
      style: 'wake',
      target: 'vehicle',
      clientId
    })
    broadcast('tts', {
      text: spoken.text,
      style: 'wake',
      target: 'vehicle',
      audioBase64: spoken.audioBase64,
      clientId
    })
    broadcast('wake_armed', { clientId, until: Date.now() + cfg.wakeArmMs })
    // if utterance is ONLY wake word, stop here
    const stripped = text.replace(/玖物[，,]?玖物|九物九物/g, '').trim()
    if (!stripped) {
      return { succeed: true, intent: { name: 'WAKE' }, utterance: wakeText }
    }
  }

  const intent = parseIntent(text)
  const result = await executor.handle(intent, { clientId, channel, wakeOk })
  const utterance = speakSvc.render(result.utteranceKey, result.utteranceParams || {})
  const target = speakSvc.resolveTarget(channel, result.speakKind)
  const spoken = await speakSvc.speak({
    text: utterance,
    style: result.speakStyle,
    target,
    clientId
  })
  broadcast('intent', {
    clientId,
    channel,
    intent,
    ok: result.ok,
    errorCode: result.errorCode,
    utterance
  })
  broadcast('tts', {
    text: spoken.text,
    style: result.speakStyle,
    target,
    audioBase64: spoken.audioBase64,
    clientId
  })
  return {
    succeed: result.ok,
    intent,
    errorCode: result.errorCode,
    utterance,
    audioBase64: spoken.audioBase64,
    target
  }
}

app.post('/api/voice/text', auth, async (req, res) => {
  try {
    const clientId = (req as any).clientId as string
    const channel = (req.body?.channel === 'cabin' ? 'cabin' : 'ptt') as VoiceChannel
    const text = String(req.body?.text || '').trim()
    if (!text) {
      res.status(400).json({ succeed: false, error: 'empty' })
      return
    }
    const out = await runUtterance(clientId, channel, text)
    res.json(out)
  } catch (e: any) {
    res.status(500).json({ succeed: false, error: e?.message || String(e) })
  }
})

app.post('/api/voice/stop', auth, async (req, res) => {
  try {
    const clientId = (req as any).clientId as string
    const out = await runUtterance(clientId, 'ptt', '停止')
    res.json(out)
  } catch (e: any) {
    res.status(500).json({ succeed: false, error: e?.message || String(e) })
  }
})

// robot read proxies (paired)
app.get('/api/state', auth, async (_req, res) => {
  try {
    res.json(await jarvis.getState())
  } catch (e: any) {
    res.status(502).json({ error: e?.message || String(e) })
  }
})

app.get('/api/map', auth, async (_req, res) => {
  try {
    res.json(await jarvis.getMap())
  } catch (e: any) {
    res.status(502).json({ error: e?.message || String(e) })
  }
})

app.get('/api/params', auth, async (_req, res) => {
  try {
    res.json(await jarvis.getParams())
  } catch (e: any) {
    res.status(502).json({ error: e?.message || String(e) })
  }
})

app.post('/api/control/:action', auth, async (req, res) => {
  const action = req.params.action
  const clientId = (req as any).clientId as string
  // map canvas autodrive/goto to task path; drive requires site
  if (action === 'drive') {
    if (!sessions.hasSite(clientId)) {
      res.status(403).json({ succeed: false, error: 'no_site' })
      return
    }
  }
  if (action === 'stop') {
    await watchdog.stop(false)
    res.json({ succeed: true })
    return
  }
  try {
    const data = await jarvis.control(action, req.body || {})
    res.json(data)
  } catch (e: any) {
    res.status(502).json({ succeed: false, error: e?.message || String(e) })
  }
})

const webDist = path.resolve(__dirname, '../../../apps/web/dist')
app.use(express.static(webDist))

const server = http.createServer(app)

// event WS for UI
const eventWss = new WebSocketServer({ noServer: true })
// jarvis proxy high/low
const highWss = new WebSocketServer({ noServer: true })
const lowWss = new WebSocketServer({ noServer: true })

function jarvisWsUrl(kind: 'high' | 'low') {
  const base = cfg.jarvis.baseUrl.replace(/^http/, 'ws').replace(/\/$/, '')
  return `${base}/ws/${kind}`
}

function pipeJarvis(clientWs: WebSocket, kind: 'high' | 'low') {
  let upstream: WebSocket | null = null
  try {
    upstream = new WebSocket(jarvisWsUrl(kind))
  } catch {
    clientWs.close()
    return
  }
  upstream.on('message', (data) => {
    if (clientWs.readyState === WebSocket.OPEN) clientWs.send(data)
  })
  upstream.on('close', () => clientWs.close())
  upstream.on('error', () => clientWs.close())
  clientWs.on('close', () => upstream?.close())
  clientWs.on('error', () => upstream?.close())
}

server.on('upgrade', (req, socket, head) => {
  const url = req.url || ''
  if (url.startsWith('/ws/events')) {
    eventWss.handleUpgrade(req, socket, head, (ws) => {
      const sock: ClientSock = { ws }
      eventClients.add(sock)
      ws.on('message', (raw) => {
        try {
          const msg = JSON.parse(String(raw))
          if (msg.type === 'auth' && msg.pairToken) {
            const rec = sessions.resolveToken(msg.pairToken)
            if (rec) sock.clientId = rec.clientId
          }
        } catch {
          /* ignore */
        }
      })
      ws.on('close', () => eventClients.delete(sock))
    })
    return
  }
  if (url.startsWith('/ws/high')) {
    highWss.handleUpgrade(req, socket, head, (ws) => pipeJarvis(ws, 'high'))
    return
  }
  if (url.startsWith('/ws/low')) {
    lowWss.handleUpgrade(req, socket, head, (ws) => pipeJarvis(ws, 'low'))
    return
  }
  socket.destroy()
})

server.listen(cfg.server.port, cfg.server.host, () => {
  console.log(
    `[forkai-gateway] http://${cfg.server.host}:${cfg.server.port} vehicle=${cfg.vehicleId} jarvis=${cfg.jarvis.baseUrl}`
  )
})
