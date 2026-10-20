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

async function mappingReady(): Promise<boolean> {
  if (!(await portOpen(19010))) return false
  try {
    const response = await fetch('http://127.0.0.1:19010/api/v1/mapping/status')
    if (!response.ok) return false
    const body = await response.json()
    return body?.capabilities?.['2d']?.online === true && body?.capabilities?.['3d']?.online === true
  } catch {
    return false
  }
}

async function ensureMapping() {
  if (await mappingReady()) return
  for (let i = 0; i < 25 && (await portOpen(19010)); i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
  if (await portOpen(19010)) throw new Error('19010 上的建图服务不是演示配置')
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'forkai-mapping-e2e-'))
  const log = fs.openSync('/tmp/forkai-mapping-e2e.log', 'a')
  if (!(await portOpen(8765))) {
    children.push(spawn(py, ['devtools/fake_bridge.py', '--port', '8765'], { cwd: path.join(root, 'services/mapping'), stdio: ['ignore', log, log] }))
  }
  children.push(spawn(py, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '19010'], {
    cwd: path.join(root, 'services/mapping'),
    env: {
      ...process.env,
      FORKAI_MAPPING_CONFIG: path.join(root, 'services/mapping/config/mapping.demo.yaml'),
      FORKAI_MAPPING_DATA_DIR: path.join(tmp, 'data')
    },
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
  await expect(page.getByRole('button', { name: '离线建图' })).toBeDisabled()
  await expect(page.getByRole('button', { name: '扩建' })).toBeDisabled()
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
