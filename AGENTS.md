# forkAI 项目约定（协作者与 AI Agent 必读）

## 技术栈与红线

- 唯一活跃栈是 **V2**：mock-jarvis(:8080) + forkai-core(:19000, Python/FastAPI) + llama-server(:19002) + apps/web。
- **V1（services/voice-gateway、services/speech、forkweb）已冻结禁用**：不要启动、不要修改、不要为其新增功能。相关 npm 命令仅以 `dev:legacy:*` 前缀保留作历史参考。
- V1 老服务需 node≥20 才能运行（系统 node16 会崩），这正是不应再碰它的原因之一。V2 中 mock-jarvis 用系统 node 即可；tests/e2e 的 Playwright 需 node≥20。

## 启动与停止

- 一键启动 V2：`./start-v2.sh`（幂等，日志在 `/tmp/forkai-v2/`）。
- 前端热更新：`npm run dev:web`（vite :5173，代理到 :19000）。
- 入口：http://127.0.0.1:19000/（core 静态托管 apps/web/dist）。
- 停止：`pkill -f 'mock-jarvis/src/index.js'; pkill -f 'llama-server'; pkill -f 'uvicorn app.main:app'`。

## 端口表

| 端口 | 组件 |
|---|---|
| 8080 | mock-jarvis（车端模拟） |
| 19000 | forkai-core（唯一后端） |
| 19002 | llama-server（NLU 兜底侧车） |
| 5173 | vite dev（仅前端热更新时） |

## 回归测试

- NLU 黄金语料：`cd services/core && .venv/bin/python scripts/nlu_corpus_test.py`
- LLM 直测：`cd services/core && .venv/bin/python scripts/nlu_llm_test.py`（需 llama-server）
- week2 任务回归：`cd services/core && .venv/bin/python scripts/week2_regression.py`（需 mock+core+llama 全起）

## 修改约定

- 改动 NLU 规则（`services/core/app/nlu/rules.py`）后必须跑 `nlu_corpus_test.py`。
- 改动话术模板时同步两处：`services/core/config/utterances.zh-CN.json` 与 `docs/utterance-table.md`。
