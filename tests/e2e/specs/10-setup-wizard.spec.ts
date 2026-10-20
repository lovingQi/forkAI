import { test, expect } from '@playwright/test'
import { kioskToken, resetSetupViaKiosk } from '../helpers'

test('用例10：初始向导', async ({ page, request }) => {
  await resetSetupViaKiosk(request)
  await page.goto(`/?kiosk=${encodeURIComponent(kioskToken())}`)
  await page.getByRole('button', { name: '在本屏继续' }).click()
  await expect(page.getByText('名称：')).toBeVisible()
  await page.getByRole('button', { name: '下一步' }).click()
  await expect(page.getByText('默认只读')).toBeVisible()
  await page.getByRole('button', { name: '下一步' }).click()
  await page.getByRole('button', { name: '下一步' }).click()
  await page.getByRole('button', { name: '完成并进入系统' }).click()
  await expect(page.getByText('语音控制', { exact: true })).toBeVisible()
})
