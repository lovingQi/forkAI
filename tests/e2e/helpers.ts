import fs from 'node:fs'
import path from 'node:path'
import { APIRequestContext, Page, expect } from '@playwright/test'

export function kioskToken(): string {
  const candidates = [
    path.resolve('services/core/data/console/kiosk.token'),
    path.resolve('../services/core/data/console/kiosk.token'),
    path.resolve('../../services/core/data/console/kiosk.token')
  ]
  for (const file of candidates) {
    if (fs.existsSync(file)) return fs.readFileSync(file, 'utf8').trim()
  }
  throw new Error(`kiosk.token 不存在: ${candidates.join(', ')}`)
}

/** 车载屏清掉残留现场锁，让后续解锁不必抢占。 */
export async function resetSiteViaKiosk(request: APIRequestContext) {
  const paired = await request.post('/api/pair/local', { data: { kioskToken: kioskToken() } })
  const body = await paired.json()
  expect(paired.ok(), JSON.stringify(body)).toBeTruthy()
  const headers = { Authorization: `Bearer ${body.pairToken}` }
  const ended = await request.post('/api/site/end', { headers, data: { force: true } })
  expect(ended.ok(), await ended.text()).toBeTruthy()
}

/** 标记初始向导已完成，避免功能页被向导拦住。 */
export async function ensureSetupCompleted(request: APIRequestContext) {
  const started = await request.post('/api/pair/start')
  const code = (await started.json()).code as string
  const confirmed = await request.post('/api/pair/confirm', { data: { code } })
  const token = (await confirmed.json()).pairToken as string
  const done = await request.post('/api/console/setup/complete', {
    headers: { Authorization: `Bearer ${token}` }
  })
  expect(done.ok(), await done.text()).toBeTruthy()
}

/** 把向导打回未完成，并让车载屏持有控制权，便于重置。 */
export async function resetSetupViaKiosk(request: APIRequestContext) {
  const paired = await request.post('/api/pair/local', { data: { kioskToken: kioskToken() } })
  const body = await paired.json()
  expect(paired.ok(), JSON.stringify(body)).toBeTruthy()
  const headers = { Authorization: `Bearer ${body.pairToken}` }
  await request.post('/api/site/end', { headers, data: { force: true } })
  const unlocked = await request.post('/api/site/unlock', { headers, data: { local: true } })
  expect(unlocked.ok(), await unlocked.text()).toBeTruthy()
  const reset = await request.post('/api/console/setup/reset', { headers })
  expect(reset.ok(), await reset.text()).toBeTruthy()
}

/** 配对（UI 全流程）：返回 clientId 已写入 localStorage 的已配对页面。 */
export async function pairViaUI(page: Page) {
  await page.goto('/')
  await page.getByRole('button', { name: '显示/刷新配对码' }).click()
  const code = (await page.locator('.gate-card .code').innerText()).trim()
  expect(code).toMatch(/^\d{6}$/)
  await page.getByPlaceholder('输入 6 位配对码').fill(code)
  await page.getByRole('button', { name: '确认配对' }).click()
  await expect(page.getByText('已配对', { exact: true })).toBeVisible()
  return code
}

/** 现场解锁（UI 对话框）：先清锁，再输入车端码解锁。 */
export async function unlockSiteViaUI(page: Page, request: APIRequestContext) {
  await resetSiteViaKiosk(request)
  await page.getByRole('button', { name: '现场解锁' }).click()
  const dialog = page.locator('.el-dialog')
  await expect(dialog).toBeVisible()
  const codeText = await dialog.locator('p').first().innerText()
  const code = codeText.replace(/\D/g, '')
  expect(code).toMatch(/^\d{6}$/)
  await dialog.getByPlaceholder('输入现场码或使用扫码 nonce').fill(code)
  await dialog.getByRole('button', { name: '解锁', exact: true }).click()
  await expect(page.getByText(/现场 \d+ 分钟/)).toBeVisible()
}
