# forkAI 项目参考

## 目录

1. 项目边界
2. 模块与事实来源
3. 运行环境
4. 变更验证矩阵
5. 已知未闭环项

## 项目边界

- 仓库：`/home/xbl/Desktop/learn/forkAI`
- 产品：全离线中文叉车语音控制、监控和任务流编排。
- 唯一活跃栈：`apps/web` + `services/core` + `services/mock-jarvis` + `services/llm-sidecar`。
- 冻结栈：`services/voice-gateway`、`services/speech` 和历史 `forkweb`。不得启动、修改或新增功能。
- 真实车端：`/home/xbl/Desktop/jarvis-fork` 提供 HTTP `/api/*` 与 WS `/ws/high|low`。
- 目标硬件：ARM RK3588、8 GB、Ubuntu 20.04；当前大量自动化结论来自 x86 + Mock。

始终先读取仓库根 `AGENTS.md`，其内容优先于本参考。

## 模块与事实来源

| 领域 | 主路径 | 事实来源 |
|---|---|---|
| 产品需求 | `docs/requirements-v2.md` | 冻结产品边界与指标 |
| 架构 | `docs/architecture-v2.md` | 模块、链路、模型与车端映射 |
| 实施状态 | `docs/implementation-plan-v2.md` | 已完成与未完成清单 |
| 验收 | `docs/acceptance-v2.md` | Mock 结果和真车待办 |
| API | `docs/api-v2.md` | ForkAI 对外 API 与任务 Schema |
| 话术 | `services/core/config/utterances.zh-CN.json`、`docs/utterance-table.md` | 运行配置与人工文档必须同步 |
| ASR | `services/core/app/asr` | Sherpa-ONNX 流式识别、端点与采集 |
| NLU | `services/core/app/nlu` | 规则优先、LLM 兜底与纠偏 |
| 执行与安全 | `services/core/app/executor.py`、`app/safety`、`app/session` | 动作、锁定、看门狗与急停 |
| 任务流 | `services/core/app/taskflow` | Schema、存储、匹配和状态机 |
| 车端契约 | `services/core/app/jarvis` | 客户端与 route 构造；最终语义以车端源码/实测为准 |
| 前端 | `apps/web` | Vue 3 监控、语音和 Vue Flow 编辑器 |
| Mock | `services/mock-jarvis/src/index.js` | 模拟契约，不是物理真值 |

## 运行环境

| 组件 | 端口 | 说明 |
|---|---:|---|
| mock-jarvis | 8080 | 车端模拟 |
| forkai-core | 19000 | FastAPI 唯一后端与静态托管 |
| llama-server | 19002 | Qwen NLU 兜底侧车 |
| Vite | 5173 | 仅前端热更新 |

V2 一键启动命令为 `./start-v2.sh`。前端热更新使用 `npm run dev:web`。启动服务前检查端口和现有进程；不得把 Mock 服务接到真实车辆地址。

## 变更验证矩阵

| 变更范围 | 工作目录 | 最低验证 |
|---|---|---|
| `app/nlu/rules.py`、纠偏、热词 | `services/core` | `.venv/bin/python scripts/nlu_corpus_test.py` |
| `nlu/router.py`、`llm.py`、`prompts.py` | `services/core` | NLU 黄金语料 + `.venv/bin/python scripts/nlu_llm_test.py`；注明 LLM 直测含人工评估项 |
| `executor.py`、任务参数、route 构造 | `services/core` | `.venv/bin/python scripts/week2_regression.py` + `scripts/protocol_conformance.py` |
| `taskflow/*` | `services/core` | `.venv/bin/python scripts/week3_engine_test.py` + `scripts/week4_voice_flow_test.py` |
| ASR/音频服务链路 | `services/core` | `scripts/asr_offline_test.py`、`scripts/asr_noise_test.py`、`scripts/ws_audio_test.py` 中适用项 |
| 前端 | 仓库根 | `npm run typecheck -w @forkai/web` + `npm run build -w @forkai/web` + 受影响 Playwright 用例 |
| 配对、现场锁、语音、流程编辑器、急停 UI | `tests/e2e` | Node 20+ 下运行 `npx playwright test` 或目标 spec |
| 协议映射或 Mock | `services/core` | `scripts/protocol_conformance.py` + 受影响 Week 回归；仍需判断是否要求真车 |
| 稳定性或故障 | `services/core` | `scripts/soak_test.py` 和相关故障注入；记录时长与环境 |
| 话术 | 仓库根 | 同步 JSON 与 `docs/utterance-table.md`，运行受影响 NLU/回归 |

脚本依赖不同服务，运行前读取脚本头部和 `docs/acceptance-v2.md`。不要调用根 `npm run build` 验证 V2 前端，因为该命令仍包含冻结的 V1 workspace。

## 已知未闭环项

以下状态来自现有实施和验收文档，完成相关任务时必须显式保留或关闭：

- 真车货叉和 route 完成语义仍需现场复核。
- 六任务和两条完整任务流尚需真车逐项验证。
- RK3588 上 Sherpa-ONNX RTF、线程和麦克风效果待验证。
- RK3588 上 llama.cpp 构建及 Qwen 0.5B/1.5B 选型待验证。
- 连续一天试点、任务成功率和稳定性待真车统计。
- RK3588 干净环境离线部署与全流程冒烟待执行。
- `parallel_groups` 真实并发路径和编辑器 UI 尚未完整闭环。
- 真车联调后的 `docs/test-report.md` 尚不存在。

不得因为 Week 2/3/4 Mock 回归通过而关闭这些项目。质量与发布部必须分别报告 Mock、浏览器、HIL、真车和目标硬件状态。
