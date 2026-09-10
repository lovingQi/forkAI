# forkAI V2 实施计划（方案B：统一服务重构）

来源：grilling 决策共识（2026-08-04）。本文件为执行追踪基准，执行过程中逐项勾选。

## 一、已确认决策汇总

| 领域 | 决策 |
|------|------|
| 总体架构 | 方案B：Python FastAPI 统一后端（forkai-core），替代 voice-gateway + speech |
| ASR | Sherpa-ONNX，RK3588 CPU推理，流式 zipformer 中文 int8 模型，16kHz，延迟<1s |
| NLP | 混合架构：规则兜底 + Qwen2-0.5B-Instruct Q4_K_M（llama.cpp llama-server 独立进程） |
| TTS | 复用 piper + zh_CN-huayan-medium，subprocess 调用 |
| 货叉控制 | 完整升降控制，通过 jarvis route 格式下发（focklift: pos/wait/tolerance） |
| 智能绕障 | 下发绕障指令给车端程序，forkAI 不直接处理绕障逻辑 |
| 任务模式 | 6种全部支持：drive(移动)、head(原地旋转)、focklift(插齿升降)、follow_back(点到点/盲叉)、get_pallet(相机栈板识别)、charge(自动充电) |
| 参数填入 | 混合模式：安全关键参数追问确认，可选参数用默认值，任何轮次可"取消" |
| 任务流编辑 | Vue Flow 流程图式（@vue-flow/core），支持分支(success/fail)/并行组 |
| 任务流执行 | 后端存储+执行引擎（FlowEngine），支持暂停/继续/取消 |
| 任务流触发 | 中文名称模糊匹配 + TTS二次确认 |
| 智能问答 | 混合：固定查询扩充 + 故障解释答案库 |
| 并发控制 | 分级锁定：点动锁 + 任务流锁；任务流running时禁止点动/新任务，查询/停止始终放行 |
| 语音体验 | 唤醒词可配置（默认"玖物玖物"）、成功/失败提示音、TTS可打断、连续对话30秒免唤醒 |
| 硬件 | ARM RK3588 / 8GB RAM / Ubuntu 20.04 |
| 测试策略 | 关键功能真车验证（货叉/6任务/任务流），其余 mock |
| 异常处理 | 网络中断任务流续跑+状态缓存；低电量(20%可配)自动挂起并充电；实体急停后强制重新现场解锁；仅中文 |
| forkweb | 冻结当前版本，不跟随更新 |
| 隐私安全 | 全离线；记录文本不录音频，7天删除；配对码一次性，现场码每次解锁后轮换 |
| 验收标准 | 试点可用：功能完整，试点稳定运行，ASR延迟<1s，任务成功率>95% |
| 文档 | 标准集：需求冻结+架构设计+API文档+用户手册+部署手册+验收清单+测试报告 |

## 二、目标目录结构

```
services/core/                    # Python FastAPI 统一后端
├── requirements.txt
├── app/
│   ├── main.py                   # FastAPI入口：HTTP路由 + WS + 静态托管前端dist
│   ├── config.py                 # load_config(): config/core.config.yaml + 环境变量覆盖
│   ├── asr/
│   │   ├── engine.py             # SherpaASR: sherpa-onnx 流式识别引擎封装（单例）
│   │   └── stream.py             # ASRStream: 每客户端音频流状态机
│   ├── tts/
│   │   └── piper.py              # synthesize(text, style) -> wav base64
│   ├── nlu/
│   │   ├── rules.py              # parse_intent_rule(): 正则规则
│   │   ├── llm.py                # LLMClient: llama-server OpenAI兼容API
│   │   ├── prompts.py            # 意图/参数抽取 prompt 模板
│   │   └── router.py             # parse_intent(): 规则→LLM→UNKNOWN
│   ├── session/
│   │   └── manager.py            # SessionManager: 配对/现场锁/唤醒武装
│   ├── safety/
│   │   └── watchdog.py           # MotionWatchdog: asyncio定时器
│   ├── jarvis/
│   │   ├── client.py             # JarvisClient: httpx异步 + WS代理
│   │   └── routes_builder.py     # build_route(task_type, params) -> jarvis route JSON
│   ├── tasks/
│   │   ├── schemas.py            # TASK_SCHEMAS: 6任务参数定义
│   │   └── dialogue.py           # ParamDialogue: 缺参追问状态机
│   ├── taskflow/
│   │   ├── schema.py             # 任务流JSON Schema + 校验
│   │   ├── store.py              # FlowStore: CRUD, data/flows/{id}.json
│   │   ├── engine.py             # FlowEngine: start/pause/resume/cancel
│   │   └── matcher.py            # match_flow_name(): 中文模糊匹配
│   └── api/
│       ├── routes_pair.py        # /api/pair/start|pending|confirm
│       ├── routes_site.py        # /api/site, /api/site/unlock, /api/site/end
│       ├── routes_voice.py       # /api/voice/text|stop, WS /ws/audio
│       ├── routes_robot.py       # /api/state|map|params, /api/control/{action}
│       ├── routes_flows.py       # /api/flows CRUD + start|pause|resume|cancel
│       └── ws_events.py          # /ws/events + /ws/high|low 代理
├── data/flows/
├── models/{asr,llm,piper}/
├── config/core.config.yaml
services/llm-sidecar/run-llama-server.sh   # llama-server :19002
services/voice-gateway/           # 冻结保留
services/speech/                  # 冻结保留，piper资源迁出
services/mock-jarvis/             # 保留；补 fork/route/任务状态模拟
apps/web/                         # 保留；新增 views/FlowEditor.vue, components/flow/*, api/flows.ts
deploy/forkai-core.service, forkai-llm.service, install.sh(更新)
docs/requirements-v2.md, architecture-v2.md, api-v2.md, user-manual.md, acceptance-v2.md, test-report.md
```

## 三、实施清单（执行追踪表）

### Week 1：后端移植 + ASR接入 + 货叉控制

- [x] 1. 创建 services/core 骨架：requirements.txt(fastapi/uvicorn/httpx/websockets/sherpa-onnx/pyyaml)、config/core.config.yaml、app/main.py（FastAPI入口、CORS、静态托管）
- [x] 2. 编写 app/config.py load_config()：yaml加载 + JARVIS_BASE_URL/FORKAI_PORT/VEHICLE_ID 环境变量覆盖
- [x] 3. 移植 app/session/manager.py SessionManager：配对码/Token/现场锁(nonce+code+force抢占)/唤醒武装，逻辑对齐 session.ts
- [x] 4. 移植 app/safety/watchdog.py MotionWatchdog：asyncio定时器 drive/stop/setSpeed/bumpSpeed，超时广播 watchdog_stop
- [x] 5. 移植 app/jarvis/client.py JarvisClient：httpx异步 getState/getMap/getParams/control
- [x] 6. 移植 app/api/routes_pair.py + routes_site.py：/api/pair/* 与 /api/site/* 全部端点
- [x] 7. 移植 app/api/routes_robot.py：/api/state|map|params + /api/control/{action}（drive需现场锁、stop直达）
- [x] 8. 移植 app/api/ws_events.py：/ws/events 广播 + /ws/high|low 双向管道代理（实现于 ws_proxy.py）
- [x] 9. 移植 app/tts/piper.py synthesize()：subprocess调piper CLI，style→语速，失败回退 audioBase64=None
- [x] 10. 移植 app/nlu/rules.py parse_intent_rule()：迁移 intent.ts 全部正则（V1词表）
- [x] 11. 移植 app/api/routes_voice.py：/api/voice/text 与 /api/voice/stop，run_utterance() 主流程
- [x] 12. V1回归：mock-jarvis下跑通闸门A验收清单7项（docs/acceptance.md）
- [x] 13. sherpa-onnx 安装与流式中文int8模型下载，验证识别延迟<1s（开发机x86_64已验证RTF≈0.06，ARM真机部署时复核）
- [x] 14. 编写 app/asr/engine.py SherpaASR：流式识别器创建/喂帧/取结果，线程池隔离CPU推理
- [x] 15. 编写 app/asr/stream.py ASRStream：客户端音频缓冲、部分/最终结果回调
- [x] 16. 扩展 routes_voice.py：WS /ws/audio 接收PCM流 → ASRStream → run_utterance()；车载常听音频采集（sounddevice读ALSA默认麦）
- [x] 17. 前端 VoiceBar 增加真实麦克风采集（AudioWorklet → WS PCM），PTT与cabin两通道联调
- [x] 18. 扩充 rules.py：货叉意图 FORK_LIFT_UP/FORK_LIFT_DOWN/FORK_LIFT_TO
- [x] 19. 编写 app/jarvis/routes_builder.py build_route()：focklift → {cmd:"focklift",pos,wait,tolerance}
- [x] 20. 扩展 mock-jarvis：接收route并模拟叉高渐变；executor接入货叉意图（高度变化大时需确认）
- [ ] 21. 真车验证①：货叉语音控制 + 观察 current_routes/status/mode 完成语义（**需现场真车，阻塞中**；~~待确认：schedulerthis 的 HTTP 映射路径与 body 结构~~ 已源码确认：POST /api/control/scheduler，body={"name","content":{"a":{cmd...}}}，完成语义 routes→TEMP_DEFAULT，成功/失败需物理量复合判定——见 docs/architecture-v2.md §4；fork 段按实际车型调范围）
- [ ] 22. Week 1出口检查：闸门A全部通过 + 真实ASR文本链路可用 + 货叉可控（mock环境已达成，待21真车复核）

### Week 2：混合NLU + 6任务 + 参数追问

- [x] 23. 编译/部署 llama.cpp llama-server（:19002），下载 Qwen2-0.5B-Instruct Q4_K_M，编写 run-llama-server.sh（x86_64源码编译完成，RK3588部署时同法aarch64编译；GGUF经ModelScope下载）
- [x] 24. 编写 app/nlu/prompts.py：意图抽取prompt（JSON输出：intent/slots/confidence），覆盖6任务+查询+复合指令（最多3动作）
- [x] 25. 编写 app/nlu/llm.py LLMClient.extract()：/v1/chat/completions，JSON解析，3s超时
- [x] 26. 编写 app/nlu/router.py parse_intent()：规则→LLM→UNKNOWN 三级路由 + 复合指令拆分（含覆盖率分流+LLM数值slot规则覆盖，0.5B实测可用但不稳，保留1.5B升级选项）
- [x] 27. 编写 app/tasks/schemas.py TASK_SCHEMAS：6任务参数定义（安全关键/默认值/追问话术）
- [x] 28. 扩充 rules.py + utterances.zh-CN.json：6任务全部触发词
- [x] 29. 扩展 routes_builder.py：6任务全部 route JSON 构造（对齐各 JMode 参数名）
- [x] 30. 编写 app/tasks/dialogue.py ParamDialogue：缺参追问→补齐→复述确认→执行；可"取消"（30s惰性超时，货叉确认已迁入）
- [x] 31. 扩展 mock-jarvis：6任务route接收与状态模拟
- [x] 32. 智能问答扩充：QUERY_SPEED/QUERY_TASK/QUERY_ALARM_EXPLAIN + 故障解释答案库
- [x] 33. 语音体验：TTS打断、连续对话30秒免唤醒（滚动续期）、成功/失败提示音（speak.beep可配，服务端PCM拼接）
- [x] 34. Week 2出口检查：6任务语音可触发 + 缺参追问可用 + 复合指令可执行（mock）——scripts/week2_regression.py 9/9 PASS

### Week 3：任务流引擎 + 流程图编辑器

- [x] 35. 编写 app/taskflow/schema.py：FlowJSON={name,nodes,edges(on:success|fail),parallel_groups} + 校验器（缩点DFS判环/入口唯一/出边约束）
- [x] 36. 编写 app/taskflow/store.py FlowStore：CRUD + data/flows/ 持久化 + 中文名称索引
- [x] 37. 编写 app/taskflow/engine.py FlowEngine：节点下发→轮询/api/state判定完成（_is_node_done 唯一判定函数，待真车校准）→按边流转；状态机六态；并行组 asyncio.gather 已实现（并发路径未经真实联调）
- [x] 38. 实现 pause/resume/cancel：pause=停当前节点并挂起；resume=重发当前节点；cancel=停车并终止（节点态扩展 paused；节点级 timeout_s 覆盖）
- [x] 39. 分级锁定：任务流running禁点动/新任务（查询/停止放行）；低电量(20%可配)自动挂起并触发charge（充满广播不自动resume）
- [x] 40. 编写 app/api/routes_flows.py：CRUD + start|pause|resume|cancel + WS节点状态推送 + GET /api/tasks/schemas
- [x] 41. 前端 @vue-flow/core@1.42.0 + controls + background，新建 src/views/FlowEditor.vue（hash路由 #/flow，最小侵入）
- [x] 42. 编写 src/components/flow/TaskNode.vue：类型图标+名称+关键参数+六态色环
- [x] 43. 编写 src/components/flow/NodePanel.vue：按 TASK_SCHEMAS 动态渲染参数表单
- [x] 44. 编写 src/api/flows.ts + 保存/加载/中文名称校验（ui.positions/viewport 持久化，后端容忍未知顶层键）
- [x] 45. 分支连线条件（成功绿实线/失败红虚线，点击切换）+ 执行时节点状态实时高亮（flow_event WS）；运行中画布只读
- [x] 46. Week 3出口检查：API级全过（顺序/分支/pause/resume/cancel/低电量/分级锁定/回归9/9）；**画布交互未做浏览器自动化验证**（需人工或Playwright）；parallel_groups 编辑器UI编排留作增强

### Week 4：语音触发任务流 + 联调 + 文档

- [x] 47. 编写 app/taskflow/matcher.py match_flow_name()：中文去噪+包含+编辑距离（阈值0.6，歧义分差<0.15播报前两候选）
- [x] 48. 语音触发任务流："执行/开始{名称}"→模糊匹配→TTS二次确认→启动/放弃；支持"暂停/继续/取消任务"（FLOW_*四意图，ParamDialogue复用确认机制）
- [x] 49. 异常收尾：断网续跑（下发重试3次/轮询连续10次才判失败/恢复重发节点）+引擎状态快照（core重启恢复为paused）；急停上升沿强制退出现场（alarm_monitor，可配关）；配对码一次性+现场码轮换回归断言通过
- [ ] 50. 真车联调：6任务逐一验证 + 2条完整任务流跑通，记录延迟/成功率（填入 test-report.md）——**需真车**
- [ ] 51. 试点压力测试：连续1天运行，验证 成功率>95%、ASR<1s——**需真车**
- [x] 52. 编写 docs/requirements-v2.md
- [x] 53. 编写 docs/architecture-v2.md（架构图+模块职责+route完成判定依据+模型清单）
- [x] 54. 编写 docs/api-v2.md（21个REST端点+WS十事件+任务流Schema+6任务参数表+33意图表）
- [x] 55. 编写 docs/user-manual.md（语音指令大全+追问示例+任务流编辑+FAQ）
- [x] 56. 编写 docs/acceptance-v2.md 并逐项执行记录（mock全自动项已执行，真车项待填）
- [x] 57. 更新 deploy/install.sh：Miniconda Python环境 + 依赖 + 模型下载（ModelScope优先）+ llama.cpp源码编译 + forkai-core/forkai-llm 双service + 旧单元迁移 + .offline-assets离线模式
- [ ] 58. RK3588工控机干净环境部署演练 + 全流程冒烟——**需真机**
- [ ] 59. Week 4出口检查：验收清单全过 + 文档齐套 + 交付演示——**待真车项完成后收口**
- [x] 60. TTS 云端增强（路线 B）：缓存 → CosyVoice2 → piper → mock；去掉提示音；启动预热

## 四、关键风险与降级预案

| 风险 | 降级预案 |
|------|---------|
| sherpa-onnx 在 RK3588 延迟>1s | 换14M小模型 → 降采样 → 降级为PTT触发识别（放弃常听） |
| Qwen2-0.5B 意图抽取不稳定 | few-shot → JSON schema重试 → 规则+模板兜底 |
| llama.cpp aarch64 编译问题 | 预编译release二进制；仍不行则LLM延后V2.1 |
| jarvis route完成语义不符（步骤21） | mode/status字符串+超时兜底；最坏逐节点固定等待 |
| Vue Flow 分支/并行超期 | 先顺序+分支，并行组降V2.1 |
| 真车时间受限 | Mock全覆盖优先，真车只做步骤21/50/51关键验证 |
