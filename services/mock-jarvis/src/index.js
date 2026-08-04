/**
 * Mock jarvis 车端（模拟真实叉车工控机上的 jarvis HTTP/WS 服务）。
 * 仅用于无实车联调 forkAI：实现 gateway 依赖的全部端点，
 * 收到 control 指令打印日志并维护内存假状态，WS 定时推假遥测。
 * 零依赖：Node 内置 http + 手写最小 WebSocket 帧编解码。
 */
import http from 'node:http'
import crypto from 'node:crypto'

const HOST = process.env.MOCK_JARVIS_HOST || '127.0.0.1'
const PORT = Number(process.env.MOCK_JARVIS_PORT || 8080)

// ---- 内存假车况 ----
const state = {
  name: 'fork-01',
  ip: '127.0.0.1',
  robot_type: 'forklift',
  mode: 'idle', // idle | moving | goto | dock
  status: '空闲',
  map_name: 'demo-map',
  score: 0.98,
  battery: 87,
  charing: false,
  ctrl_mode: 0,
  safe: true,
  motor: true,
  alarm: 'normal', // normal | estop | lost | stuck
  pose: [12.5, 3.2, 1.57],
  vel: [0, 0, 0],
  fork_height: 120,
  speed: 20
}

function lowPayload() {
  return {
    type: 'low',
    name: state.name,
    ip: state.ip,
    robot_type: state.robot_type,
    mode: state.mode,
    status: state.status,
    map_name: state.map_name,
    score: state.score,
    battery: state.battery,
    charing: state.charing,
    ctrl_mode: state.ctrl_mode,
    safe: state.safe,
    motor: state.motor,
    alarm: state.alarm,
    current_routes: {},
    fork_info: { fork_height: state.fork_height },
    input: [],
    output: [],
    virtual: []
  }
}

function highPayload() {
  return {
    type: 'high',
    vel: state.vel.join(','),
    pose: state.pose.join(','),
    laser_data: { data: [] },
    path_points: { points: [] },
    clearances: { points: [] },
    robot_size: { width: 0.9, length: 2.2, length_front: 1.2, length_rear: 1.0 }
  }
}

// GET /api/state 返回合并视图（executor 查询与前端 loadInitial 都用它）
function statePayload() {
  return {
    ...lowPayload(),
    pose: state.pose.join(','),
    vel: state.vel.join(',')
  }
}

// ---- control 指令处理 ----
function handleControl(action, payload) {
  const p = payload || {}
  console.log(`【mock车】收到指令 action=${action} payload=${JSON.stringify(p)}`)
  switch (action) {
    case 'drive': {
      const trans = Number(p.trans || 0)
      const rot = Number(p.rot || 0)
      state.speed = Number(p.speed || state.speed)
      state.mode = 'moving'
      state.status = trans > 0 ? '前进' : trans < 0 ? '后退' : rot !== 0 ? '转向' : '点动'
      state.vel = [trans * (state.speed / 100), 0, rot * 0.5]
      break
    }
    case 'stop':
      state.mode = 'idle'
      state.status = '空闲'
      state.vel = [0, 0, 0]
      break
    case 'idle':
      state.mode = 'idle'
      state.status = '待机'
      state.vel = [0, 0, 0]
      break
    case 'dock':
      state.mode = 'dock'
      state.status = '回充中'
      state.vel = [0, 0, 0]
      break
    case 'goto':
      state.mode = 'goto'
      state.status = `前往${p.goal || p.target || '目标点'}`
      break
    default:
      console.log(`【mock车】未识别 action=${action}，仍返回成功`)
  }
  return { succeed: true, action, ts: Date.now() }
}

// ---- HTTP ----
function sendJson(res, code, obj) {
  const body = JSON.stringify(obj)
  res.writeHead(code, {
    'Content-Type': 'application/json; charset=utf-8',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    'Access-Control-Allow-Methods': 'GET,POST,OPTIONS'
  })
  res.end(body)
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`)
  const path = url.pathname

  if (req.method === 'OPTIONS') return sendJson(res, 200, {})

  if (req.method === 'GET' && path === '/api/state') return sendJson(res, 200, statePayload())
  if (req.method === 'GET' && path === '/api/map') {
    return sendJson(res, 200, { name: state.map_name, data: null })
  }
  if (req.method === 'GET' && path === '/api/params') return sendJson(res, 200, { params: {} })

  if (req.method === 'POST' && path.startsWith('/api/control/')) {
    const action = decodeURIComponent(path.slice('/api/control/'.length))
    let body = ''
    req.on('data', (c) => (body += c))
    req.on('end', () => {
      let payload = {}
      try {
        payload = body ? JSON.parse(body) : {}
      } catch {
        /* ignore */
      }
      sendJson(res, 200, handleControl(action, payload))
    })
    return
  }

  sendJson(res, 404, { succeed: false, error: 'not_found', path })
})

// ---- 最小 WebSocket 服务端（仅服务端发文本帧 + 收 close/ping）----
const WS_MAGIC = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

function wsAccept(key) {
  return crypto.createHash('sha1').update(key + WS_MAGIC).digest('base64')
}

function encodeTextFrame(str) {
  const payload = Buffer.from(str, 'utf8')
  const len = payload.length
  let header
  if (len < 126) {
    header = Buffer.from([0x81, len])
  } else if (len < 65536) {
    header = Buffer.alloc(4)
    header[0] = 0x81
    header[1] = 126
    header.writeUInt16BE(len, 2)
  } else {
    header = Buffer.alloc(10)
    header[0] = 0x81
    header[1] = 127
    header.writeBigUInt64BE(BigInt(len), 2)
  }
  return Buffer.concat([header, payload])
}

server.on('upgrade', (req, socket) => {
  const url = req.url || ''
  if (!url.startsWith('/ws/high') && !url.startsWith('/ws/low')) {
    socket.destroy()
    return
  }
  const key = req.headers['sec-websocket-key']
  if (!key) {
    socket.destroy()
    return
  }
  socket.write(
    'HTTP/1.1 101 Switching Protocols\r\n' +
      'Upgrade: websocket\r\n' +
      'Connection: Upgrade\r\n' +
      `Sec-WebSocket-Accept: ${wsAccept(key)}\r\n\r\n`
  )
  socket.setNoDelay(true)

  const kind = url.startsWith('/ws/high') ? 'high' : 'low'
  console.log(`【mock车】WS /ws/${kind} 客户端已连接`)
  const timer = setInterval(() => {
    const frame = encodeTextFrame(JSON.stringify(kind === 'high' ? highPayload() : lowPayload()))
    if (!socket.destroyed) socket.write(frame)
  }, 500)

  socket.on('data', () => {})
  socket.on('close', () => clearInterval(timer))
  socket.on('error', () => clearInterval(timer))
})

server.listen(PORT, HOST, () => {
  console.log(`[mock-jarvis] http://${HOST}:${PORT} 已就绪（模拟车端，无实车）`)
})
