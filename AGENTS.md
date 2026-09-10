# forkAI 项目约定（协作者与 AI Agent 必读）

## 技术栈与红线

- 唯一活跃栈是 **V2**：mock-jarvis(:8080) + forkai-core(:19000) + apps/web。llama-server(:19002) 侧车保留但不作为运行时 NLU。
- **V1（services/voice-gateway、services/speech、forkweb）已冻结禁用**：不要启动、不要修改、不要为其新增功能。相关 npm 命令仅以 `dev:legacy:*` 前缀保留作历史参考。
- V1 老服务需 node≥20 才能运行（系统 node16 会崩），这正是不应再碰它的原因之一。V2 中 mock-jarvis 用系统 node 即可；tests/e2e 的 Playwright 需 node≥20。

## 启动与停止

- 一键启动 V2：`./start-v2.sh`（幂等，日志在 `/tmp/forkai-v2/`）。
- 云端 TTS/ASR/SiliconFlow LLM 需先 `export FORKAI_TTS_API_KEY=...`；官方 DeepSeek 另需 `export FORKAI_DEEPSEEK_API_KEY=...`（也可写在 gitignore 的 `services/core/config/secrets.yaml`，仅当环境变量为空时注入）。未设置 TTS key 时 TTS 回退 piper，ASR 报识别失败。
- 前端热更新：`npm run dev:web`（vite :5173，代理到 :19000）。
- 入口：http://127.0.0.1:19000/（core 静态托管 apps/web/dist）。
- 停止：`pkill -f 'mock-jarvis/src/index.js'; pkill -f 'uvicorn app.main:app'`。

## 端口表

| 端口 | 组件 |
|---|---|
| 8080 | mock-jarvis（车端模拟） |
| 19000 | forkai-core（唯一后端） |
| 19002 | llama-server（历史 NLU 侧车，运行时不再拉起） |
| 5173 | vite dev（仅前端热更新时） |

## 回归测试

- NLU 黄金语料：`cd services/core && .venv/bin/python scripts/nlu_corpus_test.py`
- LLM 直测：`cd services/core && .venv/bin/python scripts/nlu_llm_test.py`（需云端 LLM key）
- week2 任务回归：`cd services/core && .venv/bin/python scripts/week2_regression.py`（需 mock+core）

## 修改约定

- 改动 NLU 规则（`services/core/app/nlu/rules.py`）后必须跑 `nlu_corpus_test.py`。
- 改动话术模板时同步两处：`services/core/config/utterances.zh-CN.json` 与 `docs/utterance-table.md`。

## AI 研发工作室

- 跨模块开发、车端协议、安全、真车或交付任务使用全局 Skill `$run-forklift-ai-studio` 组织；本文件的项目事实和更高优先级指令始终优先。
- 工作室常驻一个总负责人和七个部门负责人：产品与验收、系统架构、车端协议、功能安全、AI 语音、应用工程、质量与发布。
- 执行 Agent 负责分析或实现，部门负责人负责方案合理性、风险、证据和交接条件的评审收口。
- 部门负责人直接参与实现时不得单独自审，必须由另一个受影响部门负责人交叉复核。
- 跨部门变更必须取得全部受影响部门负责人结论；存在未关闭 P0/P1 风险时不得宣布完成。

## 变更分级与审批

- L0：只读解释或诊断；L1：非安全关键单模块变化；L2：跨模块、跨服务或跨仓变化；L3：需求、安全、物理动作、重大架构、真车或部署变化。
- L0-L2 只能在用户已经批准的任务边界内自动分派。实际范围超出批准边界时立即停止并重新确认。
- 改变产品语义、验收条件、架构边界、现场锁、看门狗、急停、速度、故障降级或车辆动作时，必须取得用户明确批准。
- 未经明确批准，不启动或控制真实车辆，不修改真车配置，不在目标工控机部署。
- 不得用工作室流程覆盖 RIPER-5、用户当前模式或其他更高优先级指令。

## 事实来源与跨仓契约

- 产品范围以 `docs/requirements-v2.md` 为准；实施状态以 `docs/implementation-plan-v2.md` 为准；验证状态以 `docs/acceptance-v2.md` 和实际测试输出为准。
- 周期计划基线以 `docs/project-plan-v2.md`（已冻结）为准；执行状态（阶段/任务进度、测试日消耗、证据链接、风险状态）以 `docs/project-ledger.md` 为准，读写规范见 `.agents/skills/forkai-ledger/SKILL.md`。
- ForkAI 的车端协议实现位于 `services/core/app/jarvis`，但真实协议以 `/home/xbl/Desktop/jarvis-fork` 当前源码和指定车型实测为最终依据。
- 修改 Jarvis HTTP/WS、route、状态、单位或完成语义时，必须由系统架构部、车端协议部、应用工程部和质量与发布部会签；涉及物理动作时再加入功能安全部。
- 同步检查 `JarvisClient`、`routes_builder`、`FlowEngine`、mock-jarvis、协议测试和相关文档，禁止只改协议一侧。

## 变更验证矩阵

| 变更范围 | 最低验证 |
|---|---|
| `app/nlu/rules.py`、纠偏、热词 | `nlu_corpus_test.py` |
| `nlu/router.py`、`llm.py`、`prompts.py` | NLU 黄金语料 + `nlu_llm_test.py`，注明其中的人工评估项 |
| `executor.py`、任务参数、route 构造 | `week2_regression.py` + `protocol_conformance.py` |
| `taskflow/*` | `week3_engine_test.py` + `week4_voice_flow_test.py` |
| ASR、端点、音频链路 | `asr_cloud_test.py` + `ws_audio_test.py` |
| `asr/cloud.py` | `asr_cloud_test.py` |
| `apps/web` | Web typecheck + Web build + 受影响 Playwright 用例 |
| 配对、现场锁、语音、流程编辑器、急停 UI | Node 20+ 下运行受影响 `tests/e2e/specs` |
| Mock 或车端协议映射 | `protocol_conformance.py` + 受影响 Week 回归；必要时保留真车待验状态 |
| `tts/*` | `tts_cloud_test.py` + `week2_regression.py` |
| 稳定性、故障处理 | `soak_test.py` 或对应故障注入，记录时长与环境 |

- 运行脚本前读取脚本头部的服务前置条件。只运行与变更相关的验证，但必须报告跳过项和原因。
- 不使用根 `npm run build` 作为 V2 前端验证，因为该命令仍包含冻结的 V1 workspace；使用 Web workspace 的 typecheck/build 命令。

## 证据与收口

- 明确区分静态/单元、Mock、浏览器 E2E、HIL、真车和 RK3588 目标硬件证据。
- Mock 回归通过不能关闭真车 route、六任务、完整任务流、现场噪声、RK3588 性能、干净部署和耐久测试待办。
- 每个部门负责人必须给出 `通过`、`有条件通过` 或 `驳回`，并记录范围、依据、风险、验证证据、未验证项和交接条件。
- 最终交付必须列出修改文件、实际测试、未执行测试、遗留风险和仍需用户批准的动作。
