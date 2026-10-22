import { test, expect } from '@playwright/test'
import { ensureSetupCompleted, pairViaUI, unlockSiteViaUI } from '../helpers'

test('用例9：参数修改与备份', async ({ page, request }) => {
  await ensureSetupCompleted(request)
  await pairViaUI(page)
  await unlockSiteViaUI(page, request)
  await page.getByRole('link', { name: '参数', exact: true }).click()
  await expect(page).toHaveURL(/#\/params/)

  await expect(page.getByTestId('vehicle-param-view')).toBeVisible()
  await page.getByRole('button', { name: 'avoid' }).click()
  await page.getByRole('button', { name: '修改', exact: true }).click()
  const row = page.locator('.row', { hasText: '前方避障最大距离' })
  const input = row.locator('input').first()
  await expect(input).toBeEnabled()
  const before = await input.inputValue()
  const next = before === '2800' ? '2500' : '2800'
  await input.fill(next)
  await input.blur()
  await expect(page.getByTestId('vehicle-param-view')).toHaveAttribute('data-draft', '1')
  await page.getByRole('button', { name: '保存' }).click()
  const dialog = page.getByRole('dialog', { name: '确认参数改动' })
  await expect(dialog.getByText(before, { exact: true })).toBeVisible()
  await expect(dialog.getByText(next, { exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: '确认保存' }).click()
  await expect(page.getByText('已保存')).toBeVisible()
  await expect(page.getByTestId('vehicle-param-view')).toHaveAttribute('data-draft', '0')

  await page.getByRole('button', { name: '备份与恢复' }).click()
  await expect(page.getByRole('button', { name: '恢复' }).first()).toBeVisible()
})
