/**
 * 一次性抓取领导汇报用系统截图（需 mock-jarvis + core 已启动）。
 * 在 tests/e2e 目录用本地 playwright 运行：
 *   node ../../docs/briefing-assets/capture-screens.mjs
 */
import { chromium } from 'playwright'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const outDir = __dirname
const baseURL = 'http://127.0.0.1:19000'

async function pairViaUI(page) {
  await page.goto(baseURL + '/')
  await page.getByRole('button', { name: '显示/刷新配对码' }).click()
  const code = (await page.locator('.gate-card .code').innerText()).trim()
  await page.getByPlaceholder('输入 6 位配对码').fill(code)
  await page.getByRole('button', { name: '确认配对' }).click()
  await page.getByText('已配对', { exact: true }).waitFor()
}

async function unlockSiteViaUI(page) {
  await page.getByRole('button', { name: '现场解锁' }).click()
  const dialog = page.locator('.el-dialog')
  await dialog.waitFor({ state: 'visible' })
  const codeText = await dialog.locator('p').first().innerText()
  const code = codeText.replace(/\D/g, '')
  await dialog.getByPlaceholder('输入现场码或使用扫码 nonce').fill(code)
  await dialog.getByRole('button', { name: '解锁', exact: true }).click()
  const confirmBtn = page.locator('.el-message-box .el-button--primary')
  try {
    await confirmBtn.waitFor({ state: 'visible', timeout: 2000 })
    await confirmBtn.click()
  } catch {
    /* no force dialog */
  }
  await page.getByText(/现场 \d+ 分钟/).waitFor()
}

async function connectNodes(page, fromIdx, toIdx) {
  const from = page.locator('.task-node').nth(fromIdx).locator('.vue-flow__handle-right')
  const to = page.locator('.task-node').nth(toIdx).locator('.vue-flow__handle-left')
  const fb = await from.boundingBox()
  const tb = await to.boundingBox()
  await page.mouse.move(fb.x + fb.width / 2, fb.y + fb.height / 2)
  await page.mouse.down()
  await page.mouse.move(tb.x + tb.width / 2, tb.y + tb.height / 2, { steps: 10 })
  await page.mouse.up()
}

async function fillParam(page, label, value) {
  const item = page.locator('.node-panel .el-form-item', { hasText: label }).first()
  await item.locator('input').first().fill(value)
  await item.locator('input').first().blur()
}

const browser = await chromium.launch({
  headless: true,
  args: [
    '--use-fake-ui-for-media-stream',
    '--use-fake-device-for-media-stream',
    '--autoplay-policy=no-user-gesture-required'
  ]
})
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

// 1. 配对门禁页
await page.goto(baseURL + '/')
await page.getByRole('button', { name: '显示/刷新配对码' }).click()
await page.waitForTimeout(400)
await page.screenshot({ path: path.join(outDir, '01-pair-gate.png'), fullPage: false })

// 2. 配对 + 解锁后总览
await pairViaUI(page)
await unlockSiteViaUI(page)
await page.waitForTimeout(500)
await page.screenshot({ path: path.join(outDir, '02-dashboard-unlocked.png'), fullPage: false })

// 3. 文本语音指令（前进）
await page.getByPlaceholder(/输入指令|文本指令|说点什么/).first().fill('前进').catch(async () => {
  // 兼容不同占位符
  const input = page.locator('input[type="text"], textarea').filter({ hasNot: page.locator('[disabled]') }).first()
  await input.fill('前进')
})
const sendBtn = page.getByRole('button', { name: /发送|执行文本|提交/ }).first()
if (await sendBtn.count()) {
  await sendBtn.click()
} else {
  await page.keyboard.press('Enter')
}
await page.waitForTimeout(1200)
await page.screenshot({ path: path.join(outDir, '03-voice-text-forward.png'), fullPage: false })

// 4. 任务流编辑器：建流并执行到 succeeded
await page.getByRole('button', { name: '任务流编辑器' }).click()
await page.waitForURL(/#\/flow/)

const token = await page.evaluate(() => localStorage.getItem('forkai_pair_token'))
const listR = await page.request.get(baseURL + '/api/flows', {
  headers: { Authorization: `Bearer ${token}` }
})
const listData = await listR.json()
for (const f of listData.flows || []) {
  if (f.name === 'E2E演示流程') {
    await page.request.delete(`${baseURL}/api/flows/${f.id}`, {
      headers: { Authorization: `Bearer ${token}` }
    })
  }
}

await page.locator('.palette').getByRole('button', { name: '货叉升降' }).click()
await fillParam(page, 'pos', '150')
await page.locator('.palette').getByRole('button', { name: '点到点/盲叉' }).click()
await fillParam(page, 'start_name', 'a点')
await fillParam(page, 'target_name', 'b点')
await page.locator('.palette').getByRole('button', { name: '货叉升降' }).click()
await fillParam(page, 'pos', '75')
await connectNodes(page, 0, 1)
await connectNodes(page, 1, 2)
await page.getByPlaceholder('流程名（支持中文）').fill('E2E演示流程')
await page.getByRole('button', { name: '保存' }).click()
await page.locator('.el-message--success', { hasText: '已保存' }).waitFor({ timeout: 10000 }).catch(() => {})
await page.waitForTimeout(400)
await page.screenshot({ path: path.join(outDir, '04-flow-editor-saved.png'), fullPage: false })

await page.getByRole('button', { name: '执行', exact: true }).click()
await page.locator('.topbar .el-tag', { hasText: 'succeeded' }).waitFor({ timeout: 30000 })
await page.waitForTimeout(400)
await page.screenshot({ path: path.join(outDir, '05-flow-executed-succeeded.png'), fullPage: false })

await browser.close()
console.log('screenshots written to', outDir)
