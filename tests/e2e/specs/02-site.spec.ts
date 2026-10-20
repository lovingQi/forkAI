import { test, expect } from '@playwright/test'
import { pairViaUI, unlockSiteViaUI, ensureSetupCompleted } from '../helpers'

test('用例2：现场解锁', async ({ page, request }) => {
  await ensureSetupCompleted(request)
  await pairViaUI(page)
  await unlockSiteViaUI(page, request)
})
