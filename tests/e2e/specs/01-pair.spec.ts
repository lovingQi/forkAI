import { test, expect } from '@playwright/test'
import { pairViaUI } from '../helpers'

test('用例1：配对流程', async ({ page }) => {
  await pairViaUI(page)
  // Dashboard 元素出现
  await expect(page.getByText('语音控制', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '按住说话 (PTT)' })).toBeVisible()
})
