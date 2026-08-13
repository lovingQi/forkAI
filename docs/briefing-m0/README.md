# forkAI V2 阶段汇报材料（M0 → MVP）

## 打开方式

1. 用浏览器打开本目录下的 [`index.html`](./index.html)（`file://` 即可，无需起服务）。
2. **← / → / 空格**：翻页；**F**：全屏；点击左/右约 1/3 屏也可翻页。
3. **正片 12 页**口播；翻过第 12 页后为**附录**（全量指令表，默认不口播）。

## 口径（必读）

- 截图与演示环境为 **Mock 仿真**：`mock-jarvis(:8080)` + `forkai-core(:19000)`（可选 `llama-server:19002`）。
- **Mock / E2 回归 ≠ 真车 E5 验收 ≠ RK3588 E6**。
- 证据页角标统一为：`Mock 仿真 · 非真车验收`。

## 截图说明（`assets/`）

| 文件 | 路径 | 说明 |
|------|------|------|
| `01-site-jog-stop.png` | 配对 → 现场解锁 →「前进」→「停止」 | 现场锁 + 点动 |
| `02-fork-150mm.png` | 「升到150毫米」 | 货叉调高成功话术 |
| `03-flow-editor.png` | `#/flow` 加载「盲叉取货演示流程」 | 任务流编辑器 |
| `04-query-battery.png` | 「电量多少」 | 问答（一般仅需配对） |

抓取时间：2026-08-12；入口：`http://127.0.0.1:19000/`（`apps/web/dist` 静态托管）。

可选重抓：环境起好后执行（需本机已装 Playwright Chromium）：

```bash
# 仅文档辅助脚本，非产品测试
NODE_PATH=tests/e2e/node_modules node docs/briefing-m0/_capture_screens.mjs
```

本次实际抓取使用 Cursor 内置浏览器完成，结果已复制到 `assets/`。

## 事实来源

- 进度 / 里程碑 / 风险：`docs/project-ledger.md`
- 范围 / 双轨现状：`docs/prd.md`
- 架构：`docs/architecture-v2.md`

## 不在本材料内

- 不更新项目台账（除非另有指令）
- 不声称真车或 RK3588 已验收
