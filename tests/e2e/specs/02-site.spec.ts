import { test, expect } from '@playwright/test'
import { pairViaUI, unlockSiteViaUI } from '../helpers'

test('用例2：现场解锁', async ({ page }) => {
  await pairViaUI(page)
  await unlockSiteViaUI(page)
})
