# forkAI E2E 测试（Playwright）

浏览器端端到端测试，覆盖配对/解锁/语音/任务流编辑器/急停。

## 运行

```bash
# 1. 构建前端（core 静态托管 dist）
npm run build -w @forkai/web

# 2. 启动 mock + core
node services/mock-jarvis/src/index.js &
cd services/core && ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 19000 &

# 3. 跑测试（需 Node 20，用项目内 .node20）
export PATH="$PWD/../../.node20/bin:$PATH"   # 若已在 tests/e2e 则调整
cd tests/e2e
npx playwright test
```

## 用例

| 文件 | 内容 |
|------|------|
| 01-pair | 配对流程（UI 全流程） |
| 02-site | 现场解锁（含 409 抢占确认） |
| 03-voice-text | 文本指令链路（前进/停止话术） |
| 04-ptt-audio | PTT 音频链路（假麦，验证链路走通+优雅降级） |
| 05-flow-editor | 任务流编辑器（加节点/填参/连线/保存/执行/暂停/继续/取消/刷新还原） |
| 06-estop | 急停强制退出现场 |

## 说明

- 假麦（`--use-fake-device-for-media-stream`）提供静音正弦波，ASR 识别为空——用例4 验证链路连通与优雅降级，不验证识别准确率（识别准确率由 services/core/scripts/asr_offline_test.py 与噪声测试覆盖）。
- 失败截图存 `test-results/`。
- 用例5 会创建"E2E演示流程"，测试开头自动清理同名历史流程。
