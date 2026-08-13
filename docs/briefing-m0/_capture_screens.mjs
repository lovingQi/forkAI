/**
 * One-off Mock screenshot capture for briefing-m0 (not a product test).
 * Env: mock-jarvis + forkai-core on :19000
 */
import { createRequire } from 'module'
import { existsSync } from 'fs'
import { mkdir } from 'fs/promises'
import path from 'path'
import { fileURLToPath } from 'url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const ASSETS = path.join(__dirname, 'assets')
const BASE = process.env.FORKAI_URL || 'http://127.0.0.1:19000/'

function loadPlaywright() {
  const candidates = [
    '/home/xbl/forkAI/tests/e2e/node_modules/playwright',
    path.join(process.cwd(), 'node_modules/playwright'),
    path.join(process.cwd(), 'tests/e2e/node_modules/playwright'),
  ]
  for (const c of candidates) {
    if (existsSync(path.join(c, 'package.json'))) {
      return createRequire(path.join(c, 'package.json'))(c)
    }
  }
  throw new Error('playwright not found')
}

const { chromium } = loadPlaywright()

async function main() {
  await mkdir(ASSETS, { recursive: true })
  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    locale: 'zh-CN',
  })
  const page = await context.newPage()
  await page.goto(BASE, { waitUntil: 'networkidle', timeout: 60000 })
  await page.evaluate(() => {
    localStorage.clear()
    sessionStorage.clear()
  })
  await page.reload({ waitUntil: 'networkidle' })

  const refreshBtn = page.getByRole('button', { name: '显示/刷新配对码' })
  await refreshBtn.click()
  await page.waitForTimeout(500)
  const codeEl = page.locator('strong.code, .code, strong').filter({ hasText: /^\d{6}$/ }).first()
  await codeEl.waitFor({ state: 'visible', timeout: 10000 })
  const pairCode = (await codeEl.innerText()).trim()
  console.log('pairCode', pairCode)
  await page.getByPlaceholder('输入 6 位配对码').fill(pairCode)
  await page.getByRole('button', { name: '确认配对' }).click()
  await page.getByText('已配对').waitFor({ timeout: 15000 })
  console.log('paired')

  await page.getByRole('button', { name: '现场解锁' }).click()
  await page.waitForTimeout(600)
  const siteCodeEl = page.locator('.el-dialog strong').first()
  await siteCodeEl.waitFor({ state: 'visible', timeout: 10000 })
  let siteCode = (await siteCodeEl.innerText()).trim()
  if (!/^\d{6}$/.test(siteCode)) {
    const token = await page.evaluate(() => localStorage.getItem('forkai_pair_token'))
    const site = await page.evaluate(async (t) => {
      const r = await fetch('/api/site', { headers: { Authorization: `Bearer ${t}` } })
      return r.json()
    }, token)
    siteCode = site.code
    console.log('site from api', site)
  }
  console.log('siteCode', siteCode)
  await page.getByPlaceholder('输入现场码或使用扫码 nonce').fill(siteCode)
  await page.locator('.el-dialog').getByRole('button', { name: '解锁', exact: true }).click()
  await page.waitForTimeout(1000)
  try {
    await page.getByText('现场点动已解锁').waitFor({ timeout: 8000 })
  } catch {
    await page.getByText(/现场 \d+ 分钟/).waitFor({ timeout: 8000 })
  }
  console.log('site unlocked')

  const textInput = page.getByPlaceholder(/例如：前进/)
  if (!(await textInput.isVisible().catch(() => false))) {
    await page.getByRole('button', { name: '文本调试' }).click()
    await textInput.waitFor({ state: 'visible' })
  }

  async function sendVoice(text) {
    await textInput.fill(text)
    await page.getByRole('button', { name: '发送' }).click()
    await page.waitForTimeout(1500)
  }

  await sendVoice('前进')
  await sendVoice('停止')
  await page.waitForTimeout(500)
  await page.screenshot({ path: path.join(ASSETS, '01-site-jog-stop.png') })
  console.log('saved 01')

  await sendVoice('升到150毫米')
  const utter = await page.locator('.utter').innerText().catch(() => '')
  const logs = await page.locator('.logs').innerText().catch(() => '')
  console.log('after fork utter', utter)
  console.log('logs', logs.slice(0, 300))
  if (/确认|是否/.test(utter + logs)) {
    await sendVoice('确认')
    await page.waitForTimeout(1000)
  }
  await page.screenshot({ path: path.join(ASSETS, '02-fork-150mm.png') })
  console.log('saved 02')

  await page.goto(BASE.replace(/\/?$/, '/') + '#/flow', { waitUntil: 'networkidle' })
  await page.waitForTimeout(2000)
  const flowName = page.getByText('盲叉取货演示流程')
  if ((await flowName.count()) > 0) {
    await flowName.first().click({ timeout: 3000 }).catch(() => {})
    await page.waitForTimeout(1000)
  }
  await page.screenshot({ path: path.join(ASSETS, '03-flow-editor.png') })
  console.log('saved 03')

  await page.goto(BASE.replace(/\/?$/, '/'), { waitUntil: 'networkidle' })
  await page.waitForTimeout(1000)
  const textInput2 = page.getByPlaceholder(/例如：前进/)
  if (!(await textInput2.isVisible().catch(() => false))) {
    await page.getByRole('button', { name: '文本调试' }).click()
  }
  await textInput2.fill('电量多少')
  await page.getByRole('button', { name: '发送' }).click()
  await page.waitForTimeout(1500)
  await page.screenshot({ path: path.join(ASSETS, '04-query-battery.png') })
  console.log('saved 04')

  await browser.close()
  console.log('done', ASSETS)
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
