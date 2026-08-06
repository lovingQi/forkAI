import { test, expect } from '@playwright/test'
import { pairViaUI, unlockSiteViaUI } from '../helpers'

test('用例3：文本指令链路', async ({ page }) => {
  await pairViaUI(page)
  await unlockSiteViaUI(page)

  const input = page.getByPlaceholder('例如：前进 / 玖物，玖物 / 电量多少')
  await input.fill('前进')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.locator('.voice-bar .utter')).toHaveText('好的，前进', { timeout: 15000 })

  await input.fill('停止')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.locator('.voice-bar .utter')).toHaveText('已停止', { timeout: 15000 })
})
