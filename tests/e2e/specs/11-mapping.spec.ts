import { expect, test } from '@playwright/test'
import { spawn, type ChildProcess } from 'node:child_process'
import fs from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { ensureSetupCompleted, pairViaUI, unlockSiteViaUI } from '../helpers'

const root = path.resolve('../..')
const py = path.join(root, 'services/mapping/.venv/bin/python')
const children: ChildProcess[] = []

function portOpen(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = net.connect(port, '127.0.0.1')
    socket.setTimeout(400)
    const done = (open: boolean) => {
      socket.destroy()
      resolve(open)
    }
    socket.once('connect', () => done(true))
    socket.once('timeout', () => done(false))
    socket.once('error', () => done(false))
  })
}

async function ensureMapping() {
  if (await portOpen(19010)) return
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'forkai-mapping-e2e-'))
  const lua = path.join(tmp, 'lua')
  fs.mkdirSync(lua)
  fs.writeFileSync(path.join(lua, 'range_odom_2d_server.lua'), 'return {}\n')
  const cfg = {
    server: { host: '127.0.0.1', port: 19010 },
    dataDir: path.join(tmp, 'data'),
    minFreeBytes: 1,
    foxglove: { url: 'ws://127.0.0.1:8765', preflightTimeoutMs: 8000, startTimeoutMs: 8000 },
    robot: { urdf: path.join(root, 'services/mapping/config/robot.urdf'), resourceId: 'fork-01' },
    engines: {
      cartographer: {
        enabled: true,
        bin: path.join(root, 'services/mapping/devtools/fake_cartographer.sh'),
        protoRoot: path.join(root, 'services/mapping/devtools/proto'),
        configDir: lua,
        configBasename: 'range_odom_2d_server.lua',
        profileRoot: path.join(root, 'services/mapping/devtools/sample-profiles'),
        endpoint: '127.0.0.1:50061',
        engineVersion: 'test',
        buildId: 'test',
        readyTimeoutMs: 10000,
        stopTimeoutMs: 3000,
        killTimeoutMs: 3000
      }
    }
  }
  const cfgPath = path.join(tmp, 'mapping.config.yaml')
  fs.writeFileSync(cfgPath, [
    `server: {host: 127.0.0.1, port: 19010}`,
    `dataDir: ${cfg.dataDir}`,
    'minFreeBytes: 1',
    'foxglove: {url: ws://127.0.0.1:8765, preflightTimeoutMs: 8000, startTimeoutMs: 8000}',
    `robot: {urdf: ${cfg.robot.urdf}, resourceId: fork-01}`,
    'engines:',
    '  cartographer:',
    '    enabled: true',
    `    bin: ${cfg.engines.cartographer.bin}`,
    `    protoRoot: ${cfg.engines.cartographer.protoRoot}`,
    `    configDir: ${lua}`,
    '    configBasename: range_odom_2d_server.lua',
    `    profileRoot: ${cfg.engines.cartographer.profileRoot}`,
    '    endpoint: 127.0.0.1:50061',
    '    engineVersion: test',
    '    buildId: test',
    '    readyTimeoutMs: 10000',
    '    stopTimeoutMs: 3000',
    '    killTimeoutMs: 3000'
  ].join('\n'))
  const log = fs.openSync('/tmp/forkai-mapping-e2e.log', 'w')
  if (!(await portOpen(8765))) {
    children.push(spawn(py, ['devtools/fake_bridge.py', '--port', '8765'], { cwd: path.join(root, 'services/mapping'), stdio: ['ignore', log, log] }))
  }
  children.push(spawn(py, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '19010'], {
    cwd: path.join(root, 'services/mapping'),
    env: { ...process.env, FORKAI_MAPPING_CONFIG: cfgPath },
    stdio: ['ignore', log, log]
  }))
  for (let i = 0; i < 50; i++) {
    if (await portOpen(19010)) return
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
  throw new Error('建图服务没有启动')
}

test.beforeAll(async () => {
  await ensureMapping()
})

test.afterAll(() => {
  for (const child of children) child.kill('SIGTERM')
})

test('用例11：建图页入口、预检和现场锁', async ({ page, request }) => {
  await ensureSetupCompleted(request)
  await pairViaUI(page)
  await page.getByRole('link', { name: '建图' }).click()
  await expect(page).toHaveURL(/#\/mapping/)
  await expect(page.getByRole('button', { name: '离线建图' })).toBeEnabled()
  await expect(page.getByRole('button', { name: '扩建' })).toBeEnabled()
  const online = page.getByRole('button', { name: '在线建图' })
  await expect(online).toBeEnabled()
  await online.click()
  await expect(page.getByRole('button', { name: '开始建图' })).toBeDisabled()
  await page.getByRole('button', { name: '重新检查' }).click()
  await expect(page.getByText('已就绪')).toBeVisible({ timeout: 15000 })
  await expect(page.getByRole('button', { name: '开始建图' })).toBeDisabled()
  await unlockSiteViaUI(page, request)
  await expect(page.getByRole('button', { name: '开始建图' })).toBeEnabled()
  await page.getByRole('button', { name: '开始建图' }).click()
  await expect(page.getByText('建图中')).toBeVisible({ timeout: 15000 })
  await expect(page.getByRole('button', { name: '前进', exact: true })).toBeVisible()
  const stop = page.waitForRequest((req) => req.url().includes('/api/console/drive/stop') && req.method() === 'POST')
  await page.getByRole('link', { name: '基本控制' }).click()
  await stop
  await page.getByRole('link', { name: '建图' }).click()
  await expect(page.getByText('建图中')).toBeVisible()
  await page.getByRole('button', { name: '取消建图' }).click()
  await page.getByRole('button', { name: '确定' }).click()
  await expect(page.getByRole('button', { name: '在线建图' })).toBeVisible({ timeout: 10000 })
})
