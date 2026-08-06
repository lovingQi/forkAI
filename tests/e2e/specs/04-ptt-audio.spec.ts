import { test, expect } from '@playwright/test'
import { pairViaUI, unlockSiteViaUI } from '../helpers'

// 假麦局限说明：--use-fake-device-for-media-stream 提供的是无声/正弦假音频，
// sherpa ASR 对静音只会识别为空，服务端不产生 final——本用例验证的是
// 「采集 → WS 上行 → 端点/手动结束 → 识别中状态」链路走通且页面优雅等待，
// 不验证识别文本（真实语音由 ws_audio_test.py 在服务端用 piper 合成音验证）。
test('用例4：PTT 音频链路（假麦）', async ({ page }) => {
  await pairViaUI(page)
  await unlockSiteViaUI(page)

  // 注意：按住后按钮文本变为"松开结束"，选择器必须文本无关
  const ptt = page.locator('.voice-bar .actions button').first()
  await ptt.dispatchEvent('mousedown') // 按住
  await page.waitForTimeout(1500)
  await ptt.dispatchEvent('mouseup') // 松开 → 发送 {"event":"end"}

  // 松开后进入"识别中…"状态，证明 WS 音频链路已建立且服务端在处理
  await expect(page.locator('.voice-bar .partial')).toContainText('识别中', { timeout: 8000 })
  // 假麦为静音：无 final 返回，页面停留在识别中/聆听态且不报错、按钮恢复可点
  await expect(page.locator('.voice-bar .actions button').first()).toBeEnabled()
})
