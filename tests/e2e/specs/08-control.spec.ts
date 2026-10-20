import { test, expect } from '@playwright/test'
import { ensureSetupCompleted, pairViaUI, unlockSiteViaUI } from '../helpers'

test('用例8：基本控制', async ({ page, request }) => {
  await ensureSetupCompleted(request)
  await pairViaUI(page)
  await unlockSiteViaUI(page, request)
  await page.getByRole('link', { name: '基本控制' }).click()
  await expect(page).toHaveURL(/#\/control/)

  const forward = page.getByRole('button', { name: '前进', exact: true })
  const box = await forward.boundingBox()
  if (!box) throw new Error('前进按钮不可见')
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.waitForTimeout(600)
  await page.mouse.up()
  await expect(page.getByText('避障开')).toBeVisible()

  await page.getByRole('button', { name: '急停' }).click()
  await expect(page.getByRole('button', { name: '解除急停' })).toBeVisible()
})
