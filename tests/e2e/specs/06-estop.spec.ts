import { test, expect } from '@playwright/test'
import { pairViaUI, unlockSiteViaUI } from '../helpers'

test('用例6：急停强制退出现场', async ({ page, request }) => {
  await pairViaUI(page)
  await unlockSiteViaUI(page)
  await expect(page.getByText(/现场 \d+ 分钟/)).toBeVisible()

  try {
    // mock 触发急停；AlarmMonitor 2s 轮询上升沿强制退出现场
    const r = await request.post('http://127.0.0.1:8080/api/debug/state', {
      data: { alarm: 'estop' }
    })
    expect(r.ok()).toBeTruthy()
    await expect(page.getByText(/现场 \d+ 分钟/)).toBeHidden({ timeout: 8000 })
  } finally {
    await request.post('http://127.0.0.1:8080/api/debug/state', { data: { alarm: 'normal' } })
  }
})
