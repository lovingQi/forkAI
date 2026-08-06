import { test, expect, Page } from '@playwright/test'
import { pairViaUI, unlockSiteViaUI } from '../helpers'

/** 从 from 节点输出 handle 拖到 to 节点输入 handle（vue-flow）。 */
async function connectNodes(page: Page, fromIdx: number, toIdx: number) {
  const from = page.locator('.task-node').nth(fromIdx).locator('.vue-flow__handle-right')
  const to = page.locator('.task-node').nth(toIdx).locator('.vue-flow__handle-left')
  const fb = await from.boundingBox()
  const tb = await to.boundingBox()
  if (!fb || !tb) throw new Error('handle 不可见')
  await page.mouse.move(fb.x + fb.width / 2, fb.y + fb.height / 2)
  await page.mouse.down()
  await page.mouse.move(tb.x + tb.width / 2, tb.y + tb.height / 2, { steps: 10 })
  await page.mouse.up()
}

/** 在右侧参数面板的指定表单项填值。 */
async function fillParam(page: Page, label: string, value: string) {
  const item = page.locator('.node-panel .el-form-item', { hasText: label }).first()
  await item.locator('input').first().fill(value)
  await item.locator('input').first().blur()
}

test('用例5：任务流编辑器', async ({ page, request }) => {
  test.setTimeout(120_000)
  await pairViaUI(page)
  await unlockSiteViaUI(page)

  // 清理历史同名流程（多次运行累积），避免加载下拉框歧义
  const token = await page.evaluate(() => localStorage.getItem('forkai_pair_token'))
  const listR = await request.get('http://127.0.0.1:19000/api/flows', {
    headers: { Authorization: `Bearer ${token}` }
  })
  const listData = await listR.json()
  for (const f of listData.flows || []) {
    if (f.name === 'E2E演示流程') {
      await request.delete(`http://127.0.0.1:19000/api/flows/${f.id}`, {
        headers: { Authorization: `Bearer ${token}` }
      })
    }
  }

  // a. 进入编辑器
  await page.getByRole('button', { name: '任务流编辑器' }).click()
  await expect(page).toHaveURL(/#\/flow/)

  // b. 加三个节点并填参（面板按钮限定在左侧面板内，避免与画布节点角色冲突）
  await page.locator('.palette').getByRole('button', { name: '货叉升降' }).click()
  await fillParam(page, 'pos', '150')

  await page.locator('.palette').getByRole('button', { name: '点到点/盲叉' }).click()
  await fillParam(page, 'start_name', 'a点')
  await fillParam(page, 'target_name', 'b点')

  await page.locator('.palette').getByRole('button', { name: '货叉升降' }).click()
  await fillParam(page, 'pos', '75')

  await expect(page.locator('.task-node')).toHaveCount(3)

  // c. 连线 n1→n2、n2→n3
  await connectNodes(page, 0, 1)
  await connectNodes(page, 1, 2)
  await expect(page.locator('.vue-flow__edge')).toHaveCount(2)

  // 保存
  await page.getByPlaceholder('流程名（支持中文）').fill('E2E演示流程')
  await page.getByRole('button', { name: '保存' }).click()
  await expect(page.locator('.el-message--success', { hasText: '已保存' })).toBeVisible()

  // d. 执行 → succeeded
  await page.getByRole('button', { name: '执行', exact: true }).click()
  await expect(page.locator('.topbar .el-tag', { hasText: 'succeeded' })).toBeVisible({ timeout: 30000 })

  // e. 再执行：暂停 → paused → 继续 → succeeded；第三次执行后取消 → cancelled
  await page.getByRole('button', { name: '执行', exact: true }).click()
  await expect(page.locator('.topbar .el-tag', { hasText: 'running' })).toBeVisible()
  await page.getByRole('button', { name: '暂停', exact: true }).click()
  await expect(page.locator('.topbar .el-tag', { hasText: 'paused' })).toBeVisible()
  // paused 是稳定终态（引擎挂起轮询），确认 UI 已稳定呈现后再继续
  await expect(page.getByRole('button', { name: '继续' })).toBeEnabled()
  await page.getByRole('button', { name: '继续' }).click()
  await expect(page.locator('.topbar .el-tag', { hasText: 'succeeded' })).toBeVisible({ timeout: 30000 })

  await page.getByRole('button', { name: '执行', exact: true }).click()
  await expect(page.locator('.topbar .el-tag', { hasText: 'running' })).toBeVisible()
  await expect(page.getByRole('button', { name: '取消', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: '取消', exact: true }).click()
  await expect(page.locator('.topbar .el-tag', { hasText: 'cancelled' })).toBeVisible({ timeout: 10000 })

  // f. 刷新页面 → 加载流程 → 节点/连线还原
  await page.reload()
  await expect(page).toHaveURL(/#\/flow/)
  await page.locator('.topbar .el-select').click()
  await page.getByRole('option', { name: /E2E演示流程/ }).first().click()
  await expect(page.locator('.task-node')).toHaveCount(3)
  await expect(page.locator('.vue-flow__edge')).toHaveCount(2)
  await expect(page.locator('.task-node').first()).toContainText('pos=150')
})
