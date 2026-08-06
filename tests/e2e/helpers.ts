import { Page, expect } from '@playwright/test'

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

/** 现场解锁（UI 对话框）：读出车端码并解锁；若被其他设备持有则确认抢占。 */
export async function unlockSiteViaUI(page: Page) {
  await page.getByRole('button', { name: '现场解锁' }).click()
  const dialog = page.locator('.el-dialog')
  await expect(dialog).toBeVisible()
  const codeText = await dialog.locator('p').first().innerText()
  const code = codeText.replace(/\D/g, '')
  expect(code).toMatch(/^\d{6}$/)
  await dialog.getByPlaceholder('输入现场码或使用扫码 nonce').fill(code)
  await dialog.getByRole('button', { name: '解锁', exact: true }).click()
  // 现场锁可能被上一个浏览器会话持有（409 held）→ 弹确认框后走强制抢占
  // （Element Plus 未配 locale 时按钮为英文 OK/Cancel，用主按钮选择器）
  const confirmBtn = page.locator('.el-message-box .el-button--primary')
  try {
    await confirmBtn.waitFor({ state: 'visible', timeout: 2000 })
    await confirmBtn.click()
  } catch {
    /* 无抢占确认框：直接解锁成功 */
  }
  await expect(page.getByText(/现场 \d+ 分钟/)).toBeVisible()
}
