import { test, expect } from '@playwright/test'
import { ensureSetupCompleted, kioskToken, resetSetupViaKiosk } from '../helpers'

test('用例7：开机自检后进入配对，车载屏显示二维码', async ({ page, request }) => {
  await ensureSetupCompleted(request)
  await page.goto('/')
  await expect(page.getByRole('button', { name: '显示/刷新配对码' })).toBeVisible()

  await resetSetupViaKiosk(request)
  await page.goto(`/?kiosk=${encodeURIComponent(kioskToken())}`)
  await expect(page.getByRole('img', { name: '接入二维码' })).toBeVisible({ timeout: 15000 })
  await expect(page.getByRole('button', { name: '在本屏继续' })).toBeVisible()

  const token = await page.evaluate(() => localStorage.getItem('forkai_pair_token') || '')
  const ticket = await request.get('/api/pair/join-ticket', {
    headers: { Authorization: `Bearer ${token}` }
  })
  expect(ticket.ok()).toBeTruthy()
  const joined = await request.post('/api/pair/join', { data: { ticket: (await ticket.json()).ticket } })
  expect(joined.ok()).toBeTruthy()
  await expect(page.getByText('手机已接入')).toBeVisible({ timeout: 8000 })
})

test.describe('品牌开场', () => {
  test.use({ contextOptions: { reducedMotion: 'no-preference' } })

  test('打开页面先看到品牌开场，自检尚未开始', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByTestId('boot-intro')).toBeVisible()
    await expect(page.getByText('[ OK ] core')).toHaveCount(0)
    await expect(page.getByRole('button', { name: '显示/刷新配对码' })).toHaveCount(0)
  })
})
