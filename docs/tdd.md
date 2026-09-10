# forkAI 技术设计文档（TDD）

| 项 | 内容 |
|---|---|
| 版本 | 1.0 |
| 日期 | 2026-08-11 |
| 状态 | 整合设计稿（基于当前实现与 jarvis-fork 源码核对） |
| 关系声明 | 本文与 `docs/architecture-v2.md` 互补：架构文档描述"是什么/怎么连"，本文描述"怎么实现的/为什么这么设计"。冲突时以代码与架构文档为准。 |
| 关联文档 | 需求 `docs/prd.md`（基线 `docs/requirements-v2.md`）、架构 `docs/architecture-v2.md`、API `docs/api-v2.md`、验收 `docs/acceptance-v2.md` |

## 1. 系统上下文

forkAI 部署在叉车车载工控机，外部实体：

| 外部实体 | 交互方式 | 说明 |
|---|---|---|
| 现场操作员 | 浏览器（车载屏/手机扫码）+ PTT / 文本 / 车载麦克风 | 主要用户 |
| jarvis 车端程序 | HTTP `/api/*` + WS `/ws/high|low`（:8080） | 真实车辆单机程序（C++/ROS），仓库 `/home/xbl/Desktop/jarvis-fork`；开发期用 mock-jarvis 模拟 |
| llama-server | HTTP OpenAI 兼容 `/v1/chat/completions`（:19002，仅本机回环） | llama.cpp 侧车，NLU 兜底 |
| 车载音频设备 | ALSA 麦克风（服务端 cabin_listen，默认关闭）、扬声器（piper 直接播放或浏览器播放） | 真车音区待实测 |

设计约束：全离线；概率模型不直接控车；车端 HTTP/WS 接口**无鉴权**（见第 7 章源码事实），鉴权与权限分级全部由 forkai-core 承担。

## 2. 组件架构

| 组件 | 技术栈 | 入口 | 端口 | 职责 |
|---|---|---|---|---|
| forkai-core | Python 3.13 + FastAPI 0.115 + uvicorn；httpx / websockets / sherpa-onnx / numpy / sounddevice | `services/core/app/main.py` | **19000** | 唯一后端：鉴权配对、现场锁、语音链路、NLU、执行器、任务流引擎、安全、TTS、车端代理、静态托管前端 |
| apps/web | Vue 3.4 + vite 4 + TS + Element Plus + Pinia + @vue-flow；hash 路由（未启用 vue-router） | `apps/web/src/main.ts` | 经 19000 访问；dev :5173 | 监控 Dashboard、语音条 VoiceBar、流程编辑器 FlowEditor |
| mock-jarvis | 零依赖 Node.js（内置 http + 手写 WS 帧编解码） | `services/mock-jarvis/src/index.js` | **8080** | 模拟车端契约，内存假状态严格对齐真车语义（非物理真值） |
| llm-sidecar | llama.cpp `llama-server` 二进制 + Qwen2-0.5B-Instruct Q4_K_M | `services/llm-sidecar/run-llama-server.sh` | **19002**（仅回环） | NLU 兜底推理；`src/` 为 llama.cpp 源码树供 RK3588 自编译 |

启动：`./start-v2.sh`（幂等拉起 mock + llama + core，日志 `/tmp/forkai-v2/`）。生产由 core 静态托管 `apps/web/dist`，入口 `http://<车IP>:19000/`。

依赖方向：`apps/web → core → {jarvis, llama-server}`；core 启动时不强依赖 jarvis/llama 可达（降级设计见第 8 章）。

## 3. forkai-core 模块设计

> 每个模块给出：职责 / 关键接口 / 状态机或数据结构 / 安全与降级 / 实现状态与代码位置。

### 3.1 装配与配置（`app/main.py`、`app/config.py`）

- **职责**：进程装配单例——EventBus、SessionManager、JarvisClient、MotionWatchdog、IntentExecutor、LLMClient、AlarmMonitor、FlowStore、FlowEngine，挂到 `app.state`；注册 6 个路由；静态托管前端。
- **关键行为**：CORS 全开（厂内局域网假设）；`UnpairedError` 全局转 401 `{"succeed":false,"error":"unpaired"}`；startup 钩子启动 `cabin_listener.start()`、`alarm_monitor.start()`、`flow_engine.restore_snapshot()`。
- **配置**：`config/core.config.yaml` + 环境变量覆盖（`FORKAI_CORE_CONFIG`/`JARVIS_BASE_URL`/`FORKAI_PORT`/`VEHICLE_ID`）。关键配置项：watchdogMs=2000、siteSessionTtl=45min、pairCode=5min、pairToken=24h、wakeArm=30s、speed 20/40/step5、fork 75~210mm、llm timeout 3s、taskflow 超时 120s/低电 20%/恢复 80%/轮询 500ms、`safety.estop_exit_site=true`。
- **实现状态**：已完成，Mock 回归。

### 3.2 鉴权依赖（`app/api/deps.py`）

- Bearer token → `SessionManager.resolve_token`；`read_body` 容错解析（非 JSON 不炸）。

### 3.3 配对（`app/api/routes_pair.py`）

- `POST /api/pair/start` 生成 6 位数字码；`GET /api/pair/pending` 车载端查看；`POST /api/pair/confirm` 校验后签发 pairToken + clientId。
- 配对码 TTL 5min、一次性（惰性过期）；token TTL 24h。

### 3.4 现场锁（`app/api/routes_site.py`、`app/session/manager.py`）

- `GET /api/site` 返回 vehicleId + 当前一次性 nonce/code + 持有者；`POST /api/site/unlock`（nonce 或 code 均可）→ 获得现场锁；409 `held` 时可 `force` 抢占（原持有者标记 stolen）；`POST /api/site/end` 释放。
- **每次解锁后 `rotate_site_codes()` 轮换一次性码**（防截图复用）；TTL 45min 惰性清理；变化广播 `site_changed`。
- SessionManager 还负责**唤醒武装**窗口（wakeArmMs 滚动，按 clientId 绑定）。**全部内存态**：core 重启配对/锁/武装/对话全部失效（已知边界 R-03）。

### 3.5 车端代理（`app/api/routes_robot.py`、`app/api/ws_proxy.py`）

- `GET /api/state|map|params` 透传 jarvis（502 包装错误）；`POST /api/control/{action}`：`drive` 需现场锁（403 `no_site`），`stop` 走 MotionWatchdog.stop，其余直转。
- `/ws/high`、`/ws/low` 双向管道代理 jarvis WS（**只下行转发**，客户端上行不转）；`/ws/events` 为 core 事件总线客户端入口（可发 auth 绑定 clientId）。

### 3.6 语音主链路（`app/api/routes_voice.py`）

- `POST /api/voice/text`：文本入口（调试/远程），走与语音相同的 `run_utterance()`。
- `WS /ws/audio`：首帧 JSON `{pairToken, channel}` 鉴权（失败 4401）；二进制 PCM16 缓冲至 `{"event":"end"}` → 云端整句 ASR；超时/HTTP 失败播 `fail_asr`；空音频或空识别（`asr_empty`）静默忽略、不播报。
- **核心函数 `run_utterance()`**（编排顺序）：
  1. cabin 通道先 `match_wake_word`：命中 → 武装 30s + TTS wake_ack + 广播 `wake_armed`；纯唤醒词**短路只应声**（ptt 通道同）。
  2. `correct_asr` 纠偏 → `parse_intent`（混合路由，可返回复合 intents 数组）。
  3. 逐个 `executor.handle`：复合指令按序执行，**失败或 awaiting 即中断后续**。
  4. render 话术 → 合成 → 广播 `intent` + `tts` 事件。
  5. cabin 通道成功后滚动续期免唤醒窗口（前端已停用常听，该分支仅 API 兼容）。
- `GET/PUT /api/voice/providers`：模型菜单与延迟探测；PUT 写 `runtime_models.yaml`。
- `POST /api/voice/stop`：等价语音"停止"。

### 3.7 NLU（`app/nlu/rules.py`、`router.py`、`llm.py`、`prompts.py`）

- **规则层（rules.py）**：`RULES` 数组先匹配先赢，顺序有互斥设计（CONFIRM/CANCEL > FLOW_* > STOP > TASK_HEAD > TURN > FORK > SPEED > TASK_* > DOCK > GOTO > QUERY）。单位换算（毫米/厘米/米）、`掉头`=180°、站点名保真。含 **ASR_CORRECTIONS** 同音误识别纠偏表（长词优先）与**唤醒词同音字等价组**字符类匹配。
- **混合路由（router.py）**：规则命中且残余无意图 → 直接返回；残余有意图 → compound 交 LLM 拆分；规则 UNKNOWN → 交 LLM。LLM 输出做 `INTENT_NAMES` 白名单校验 + 关键 slot 类型校验（`_slots_sane`，防 0.5B 硬映射）；LLM 首意图数值 slot 用规则解析值覆盖（防单位换算错）。**LLM 全废时降级**：unknown→`[UNKNOWN]`；compound→只执行首个规则命中（部分执行）。
- **LLM 客户端（llm.py）**：云端 OpenAI 兼容 `/chat/completions`，temperature=0、max_tokens=256、10s 超时、`enable_thinking=false`；剥 ```json 围栏、结构校验、≤3 项；不可达打一次 warning 后静默降级。菜单：官方 DeepSeek-flash（默认）/ chat / v4-pro，以及 SiliconFlow DeepSeek-V3.2、V3、Qwen3.5-27B、GLM-5.1。
- **意图全集**：34 个意图名（`prompts.py` INTENT_NAMES；含 e6011ae 新增 QUERY_STATUS）。
- **设计要点**：LLM 输出永远过白名单与类型校验——概率模型不直接产生可执行指令。
- **实现状态**：已完成；黄金语料 75 条 + LLM 直测（含人工评估项）。

### 3.8 ASR（`app/asr/cloud.py`、`pcm_wav.py`）

- **云端整句**：PTT 缓冲 PCM → WAV → SiliconFlow `/audio/transcriptions`；超时 5s 抛 ASRError，播 `fail_asr`，不回退 sherpa。空音频/空识别不播报（`errorCode=asr_empty`）。失败在 core 日志打 `ASR fail reason=` 或 `ASR skip reason=`（timeout / empty_transcription / http_* / empty_audio 等）及 `pcm_ms`/`peak`。
- **菜单**：`GET /v1/models?type=audio&sub_type=speech-to-text` 全部 id；打开下拉并行测延迟。
- **Sherpa / CabinListener**：代码保留，PTT 与 main 不再加载/启动。
- **实现状态**：云端 PTT 已落地。

### 3.9 TTS 与话术（`app/tts/service.py`、`cloud.py`、`cache.py`、`prewarm.py`、`piper.py`、`app/speak.py`）

- **合成链**：本地缓存 → 云端 CosyVoice2（超时 1.5s，WAV 头修正）→ piper CLI 兜底 → mock（audio=None，前端 speechSynthesis）。piper 路径保留拉丁字母→中文读音字转写；云端收原文。无提示音、无前导静音。
- **speak.py**：话术渲染（`config/utterances.zh-CN.json`，含 `fail_asr`，缺失占位符原样保留）+ 播报目标路由（wake→vehicle；query→speak.query；cabin/ptt→对应配置）。
- **实现状态**：云端增强已落地；音色/音量真机听感待验（R-08）。

### 3.10 任务 Schema 与参数对话（`app/tasks/schemas.py`、`dialogue.py`）

- **TASK_SCHEMAS**：4 个任务 schema（follow_back/get_pallet/charge/head；focklift/drive 在 routes_flows 的 `_EXTRA_NODE_SCHEMAS` 补全）——required + safety 标记 + 默认值 + 追问话术 + confirm 策略（follow_back 集齐后必须汇总确认）。
- **ParamDialogue**：按 clientId 的多轮挂起态（task collect/confirm、fork confirm、flow confirm 三类）；30s 惰性超时（晚到 CONFIRM/CANCEL 回 `timeout_cancel`）+ 120s 硬 TTL；任意轮"取消"放弃、"停止"放弃并停车；confirm 阶段收到新指令放弃旧确认按新指令处理。

### 3.11 执行器（`app/executor.py`）

- **职责**：意图分派中心，安全策略的确定性执行点。
- **安全层级**：
  - STOP 直达车端并清唤醒武装；
  - 运动（drive/turn）/货叉/TASK_* 三类需现场锁；cabin 通道另需唤醒武装或本轮 wake_ok；
  - **任务流 running|paused 时分级锁定**：运动级指令拒 `flow_running`；STOP/QUERY/CONFIRM/CANCEL 不受影响；
  - 货叉 |目标-当前|>100mm（可配）触发安全确认挂起；
  - TASK_* 缺参追问/汇总确认走 ParamDialogue。
- **QUERY_* 只读分支**：读 `get_state()` 组话术——QUERY_STATUS 聚合车况、QUERY_ALARM_EXPLAIN 答案库（estop/lost/stuck/normal）、QUERY_SPEED 的 vel 字符串兜底、QUERY_TASK 过滤 `TEMP_DEFAULT`。

### 3.12 安全机制（`app/safety/watchdog.py`、`alarm_monitor.py`）

- **MotionWatchdog**：drive 后 `loop.call_later(watchdogMs)` 定时，超时自动 `control("stop")` 并回调广播 `watchdog_stop`；速度 clamp [5, max]，JS 半进一舍入对齐。
- **AlarmMonitor**：2s 轮询 `get_state()`；`alarm=="estop"` **上升沿**强制 `end_site(force=True)` + 广播 `site_changed{holderClientId:null, reason:"estop"}` + piper 播报 `alarm_estop_exit`；恢复后重新武装；jarvis 不可达静默跳过；`safety.estop_exit_site=false` 可关闭。

### 3.13 任务流（`app/taskflow/schema.py`、`engine.py`、`store.py`、`matcher.py`）

- **Schema 校验（schema.py）**：FlowJSON = `{id,name,nodes[{id,type,params}],edges[{from,to,on:success|fail}],parallel_groups?,options?,ui?}`；校验节点类型/required 参数（复用 TASK_SCHEMAS）、边引用、每节点每类出边≤1、并行组不重叠、**入口恰好 1 个**、缩点 DFS 查环；`build_units` 把并行组缩为单元（g{i}）建邻接表；ui 字段不解析原样存取。
- **FlowEngine（engine.py，app.state 单例）**：
  - **互斥**：全局同一时刻仅一条流 running|paused（FlowBusyError → 409 already_running）。
  - **状态机**：流六态 idle/running/paused/succeeded/failed/cancelled；节点态 pending/running/paused/succeeded/failed/skipped；每次变化广播 `flow_event`。
  - **完成判定**：集中 `_judge_node` + `_physical_verdict`——`current_routes.routes` 名消失（→`TEMP_DEFAULT`）后按物理量复核：focklift 叉高容差（≥10mm）、charge 看 charing、head 角度差 ≤0.15rad；**follow_back/get_pallet 无物理量可判返回 None**（只靠 route 消失判成功）；支持"瞬时完成"；10s 启动宽限未见 running 判 failed；节点超时（默认 120s，节点/流 options 可覆盖）停车判 failed。
  - **暂停/恢复**：pause 立即 `control("stop")`、当前节点记 paused、`_pause_gen` 代数递增；resume 当前节点**从头重发** route；执行期被打断（代数变化）强制重发；cancel 停车 + task.cancel，剩余 skipped。
  - **故障韧性**：下发重试 3 次间隔 1s；轮询连续失败 ≥10 次（≈5s）判节点 failed；断连期 2s 节流广播 `flow_jarvis_lost`，恢复广播 `flow_jarvis_back` 并 `_Redispatch` 重发当前节点。
  - **低电量**：battery<20% → 自动 pause + TTS `flow_low_battery` + 自动下发 charge(goal=auto)；≥80% 广播 `flow_charge_full`，**不自动 resume**。
  - **崩溃恢复**：running/paused 迁移落盘 `data/flows/.engine_state.json`；启动 `restore_snapshot()` 恢复为 **paused** 等人工 resume；终态清快照。
  - **并行组**：`asyncio.gather` 并发，全 succeeded 才出组，任一 failed 走组级 fail 边（真实并发路径未完整闭环，R-05）。
- **FlowStore（store.py）**：`data/flows/{id}.json` 每流一文件，内存索引 + 中文名去噪索引，asyncio.Lock。
- **语音触发（matcher.py + executor）**：FLOW_START 精确 → 模糊匹配（包含 0.8 / Levenshtein ratio，阈值 0.6，top1-top2 分差 <0.15 判歧义拒答）→ ParamDialogue 确认 → engine.start。
- **实现状态**：已完成，week3 7/7、week4 9/9 Mock 回归。

### 3.14 车端客户端（`app/jarvis/client.py`、`routes_builder.py`）

- **JarvisClient**：httpx（10s 总/3s connect 超时）；`GET /api/state|map|params`（非 2xx 抛 JarvisError）；`POST /api/control/{action}`（任何状态都尝试解 JSON，仅网络异常抛错）；`start_route` = `control("scheduler", route)`。
- **routes_builder**：route 结构 `{"name": f"voice_{cmd}_{ts}", "content": {"a": {节点}}}`；5 种节点构造器（head/focklift/follow_back/get_pallet/charge），参数默认值与真车 JMode 对齐；缺 required 抛 ValueError，未知 cmd 抛 NotImplementedError；`mock_fail` 透传键用于 mock 联调失败路径（真车忽略未知键）。节点字段与真车对应关系见第 7 章。
- **EventBus（`app/events.py`）**：`{type, payload, ts}` fire-and-forget 广播，10 种事件：tts、intent、site_changed、watchdog_stop、wake_armed、asr_final、flow_event、flow_jarvis_lost、flow_jarvis_back、flow_charge_full。

### 3.15 flows API（`app/api/routes_flows.py`）

- 任务流 CRUD + `POST /api/flows/{id}/start`（409 already_running；**只需配对不要求现场锁**，注释明示真车可一处加 has_site 校验）+ `/api/flow-engine/pause|resume|cancel|status` + `GET /api/tasks/schemas`（供前端动态表单）。

## 4. 前端设计（apps/web）

- **路由**：hash 路由（`App.vue:70`）：`#/flow` → FlowEditor，否则 Dashboard；未配对显示配对门（6 位码）。头部常驻：车端连接/配对/现场剩余分钟 + 全局"停"按钮。
- **Dashboard.vue**：左 CanvasView（地图+激光+位姿+路径+车体轮廓画布，右键点"到达 X"→ `control('autodrive')`）；右 StatusPanel（车况/实时数据/叉车信息/IO 位）+ VoiceBar + 任务流入口。
- **VoiceBar.vue**：PTT 按住说话（鼠标/触摸，或按住空格、松开结束；INPUT/TEXTAREA/选择框内不抢空格）；麦克风常驻复用，按住期间先本地缓冲再上传，松手 flush 尾巴；ASR/LLM 下拉（打开时测延迟且不覆盖当前选中，改选立即写入 runtime_models.yaml，旧探测请求作废）；停止；文本调试（仅 ptt，ASR 识别结果写入该输入框，不自动发送）；识别中/话术/最近 8 条日志；麦不可用降级文本输入。常听与车载通道已去掉。
- **FlowEditor.vue**（457 行）：vue-flow 画布，6 种节点，拖拽连线（每节点每类出边限 1 条，点击边切 success/fail），NodePanel 按 `/api/tasks/schemas` 动态渲染参数表单（required/safety 标记），CRUD + 执行/暂停/继续/取消 + 引擎状态标签；节点色环随 `flow_event` 更新；布局存 `flow.ui.positions`；引擎忙时全编辑禁用；**不支持编辑 parallel_groups 和 options**（保存只序列化 nodes/edges/ui，含并行组的流再保存会丢该字段，R-05）。
- **stores/session.ts**：配对/现场/事件总线状态；TTS 用**常驻 AudioContext** 播 base64（避免 new Audio 重开流吃开头字，配合服务端前导静音垫），失败回退 speechSynthesis；`asr_final` 触发 TTS 打断；`flow_event` 更新流程状态。
- **stores/robot.ts**：high/low WS 解析进车况 state（pose/vel/laser/path/robot_size/battery/alarm/current_routes/fork_info/IO）。
- **api 层**：axios Bearer 拦截；WsChannel 2s 自动重连；MicCapture（AudioWorklet 优先、ScriptProcessor 兜底，重采样 16k PCM16，静音 Gain 挂图、松手 flush）；AudioWs 首帧 `{pairToken, channel}`。

## 5. mock-jarvis 与 llm-sidecar 设计

### 5.1 mock-jarvis（`services/mock-jarvis/src/index.js`）

- 零依赖 Node.js：内置 http + 手写 WS 文本帧编解码。
- HTTP：`GET /api/state|map|params` + `POST /api/control/{drive,stop,idle,dock,goto,scheduler,routes,...}`（统一回 `{"succeed":true}`）+ 调试端点 `POST /api/debug/state`（battery/alarm/charing 注入）。
- WS：`/ws/high` 10Hz、`/ws/low` 1Hz 推送，对齐真车频率与字段。
- 内存假状态**严格对齐真车语义**：mode=JMode 名、status="mode，子状态"、route 结束 SetDefaultRICK→`TEMP_DEFAULT`、`current_routes.status` 恒 "running"、`/api/state` 无 speed 字段；focklift 叉高 200ms/10mm 渐变模拟、head 改 pose[2]、charge 置 charing、`mock_fail` 中途夭折。
- **已知缺口**：无 `autodrive` 分支（画布右键"到达"落 default，只验链路不验语义，R-04）；`schedulerthis` 只打废弃警告。
- **定位声明**：mock 是契约模拟，不是物理真值；Mock 通过不能关闭真车待办。

### 5.2 llm-sidecar

- `bin/llama-server` 预编译二进制（仓库内未标注对应 llama.cpp 版本；deploy 用 b10256 源码编译）；模型 `services/core/models/llm/` Qwen2-0.5B-Instruct Q4_K_M；仅监听本机回环；systemd 单元带 warmup 请求使模型常驻。

## 6. 端到端数据流

### 6.1 语音指令链路（PTT）

```
浏览器 PTT 采集（AudioWorklet 16k PCM16）
  → WS /ws/audio（首帧 {pairToken, channel:"ptt"}）
  → 缓冲至 {"event":"end"} → 云端整句 ASR（5s 超时）
  → 广播 asr_final（前端据此打断当前 TTS）
  → correct_asr 纠偏 → parse_intent（规则→云端 LLM→UNKNOWN，复合拆分）
  → executor.handle（现场锁/武装/任务流互斥检查 → ParamDialogue 追问/确认）
  → JarvisClient POST /api/control/{action} 或 scheduler 内联 route
  →（drive 类）MotionWatchdog 启动 2s 倒计时
  → speak 渲染话术 → TTS（缓存 / 云端 CosyVoice2 / piper）
  → /ws/events 广播 intent + tts → 前端 AudioContext 播放
```

### 6.2 任务流执行链路

```
语音 "执行XX流程" → FLOW_START → matcher 模糊匹配 → ParamDialogue 确认
  → FlowEngine.start（全局互斥检查）
  → 循环：build_units 取当前单元 → routes_builder 构造内联 route
    → POST /api/control/scheduler → 500ms 轮询 GET /api/state
    → _judge_node（route 名 → TEMP_DEFAULT + 物理量复核）判 succeeded/failed
    → success/fail 边选路；并行组 asyncio.gather
  → 异常分支：断网（重试/续跑/lost-back 广播）、低电（暂停+充电）、超时（停车 failed）
  → 状态迁移落盘 .engine_state.json；每步广播 flow_event
```

### 6.3 配对与现场锁链路

```
POST /api/pair/start → 车载 GET /api/pair/pending 显示 6 位码
  → 用户输入 POST /api/pair/confirm → pairToken + clientId（Bearer 之后所有请求）
  → GET /api/site 取一次性现场码 → POST /api/site/unlock → 现场锁（45min，码轮换）
  → 运动级指令放行；estop 上升沿 → 强制 end_site + 播报
```

## 7. 车端协议映射（基于 jarvis-fork 源码核对）

> 事实来源：`/home/xbl/Desktop/jarvis-fork` 当前源码（行号为 2026-08-11 核对值）。JRoutes/JMode 实现在 `ext/grm` 预编译库，仅有头文件；完成语义以头文件声明 + forkAI 侧反汇编注释 + Mock 对齐为据，**真车语义最终以上车实测为准**（plan 步骤 21 未关闭）。

### 7.1 HTTP 路由注册事实（`src/service/JWebHttpServer.cpp:49-70`）

- GET 类：`/api/state`、`/api/map`、`/api/params`、`/api/routes/rules`。
- POST 类：`/api/config`、`/api/laser/enable`、`/api/control/{drive,stop,idle,dock,goto,autodrive,person_follow,routes,scheduler,localize,motor,safe,map,flap,ctrl,output}`。
- 分发：`HandleRequest`（:114-215）；`/api/control/*` 已知 action 调 `JWebService` 对应方法后**统一返回 200 `{"succeed":true}`**；未知 action 返回 404 `{"succeed":false,"error":"unknown control(...)"}`。**`succeed:true` 仅表示受理，不代表物理完成**（与 AGENTS.md 一致）。
- 服务参数：CivetWeb，`num_threads=8`，`request_timeout_ms=10000`（:32-37）。
- **无鉴权**：所有 HTTP/WS handler 无身份校验——鉴权责任完全在 forkai-core。

### 7.2 SchedulerThis 与内联 route Schema（`JWebService.cpp:815-822`、`ext/grm/inc/task/JRoutes.h:44-45`）

- `SchedulerThis`：取 body `name`，`json["routes"]=name`，**整个 packet 透传** `mRoutes->Start(json)`。
- `JRoutes::Start(JArg)` 期望结构：`{routes, key?, id?, content:{a:{cmd, goal, comment, ...}}}`；默认起始 key 为 `"a"`（JRoutes.h:223 `DefaultStart="a"`）；节点间跳转键 `success`/`fail`/`interrupt`（:224-226）。
- forkAI 构造 `{"name":..., "content":{"a":{节点}}}` 与之对齐：`SchedulerThis` 补 `routes=name` 后，`content.a` 即首个任务节点。
- 命名 route 备选：`POST /api/control/routes`，body `{routes, key, id}`，剥 ` [Temp]`/` [Template]` 后缀后 `mRoutes->Start(route,key,id)`（JWebService.cpp:791-813）。

### 7.3 route 生命周期与完成语义

- `JRoutes::GetCurTaskInfo()` 返回 keys：`id,key,routes,value,note,mode,status`（JRoutes.h:78）。
- `/api/state` 的 `current_routes`：`routes/key/id` 取自 GetCurTaskInfo，**`status` 恒为字符串 "running"**（JWebService.cpp:166-172）——不可据此判完成。
- route 结束（成功/失败/中止）后车端调 `SetDefaultRICK()` 复位为缺省任务信息；缺省 route 名为 `TEMP_DEFAULT`（`ext/grm/params/routes/routes.temp.json`；forkAI engine.py:440 注释引 libgrm 反汇编：TEMP_DEFAULT/a/{cmd:idle}）。
- **forkAI 判定策略**：观察 `current_routes.routes` 由任务名变为 `TEMP_DEFAULT`（或被顶替）判定"任务已结束"，再按任务类型物理量复核成败（focklift 叉高、charge 看 charing、head 角度差）；follow_back/get_pallet 无可用物理量，仅能按 route 消失判成功（P1 风险 R-01，待真车复核）。
- mode 返回值经 `SetRet`/`TaskRet` 传递，route 内按 success/fail/interrupt 跳转（JRoutes.h:72-73、:135-140）。

### 7.4 控制 action 语义（JWebService.cpp）

| action | body → 车端 cmd | 要点 |
|---|---|---|
| drive（:625-651） | `{trans,rot,speed}` → `safedrive`（mIsSafe）或 `drive` | 车端按 `trans*speed/100`、`rot*speed/100` 整型换算；`trans_wheel*speed/10`；经 `mRoutes->ActiveMode` |
| stop（:653-659） | — → `stop` | ActiveMode 单节点 |
| idle（:661-672） | — → `idle`，time=-1 | |
| dock（:674-714） | — → `charge` 或 `steer_charge`（舵轮车型） | 自动选最近 Dock 对象 |
| goto（:716-741） | `{target,goal,poseX,poseY,poseTh,heading}` → `goto` | 站点名或坐标 |
| autodrive（:743-760） | `{goal_name}` → `auto_drive`，start_name="any" | mock 无此分支（R-04） |
| person_follow（:762-789） | `{stop\|cmd,follow_distance,max_speed,lost_timeout}` → `person_follow`/`stop` | |
| scheduler | 见 7.2 | forkAI 六任务唯一入口 |
| routes | 见 7.2 | 命名 route |
| localize（:824-875） | `{target,goal,poseX,poseY,poseTh}` | 重定位 |
| motor / safe | `{flag}` | 电机使能 / 安全模式开关（影响 drive→safedrive） |
| map / flap / ctrl / output | 参数见源码 | 切图、挡板、控制权、IO 输出 |

cmd 与 JMode 处理者对应（源码 AddTask 注册）：`drive/safedrive`→JModeDrive_、`stop`→JModeStopWithSound、`goto`→JModeFltGoto/mg_mode_ros_goto、`auto_drive`→JModeAutoDrive、`focklift`→JModeFocklift、`get_pallet`→JModeAutoGetPallet、`follow_back`→JModeFollowBack、`charge`→JModeFltCharge/JModeCharge*。`head` 的处理者未在 `src/` 检索到（可能在预编译库），forkAI 注释记为 JModeHead——**标记：未确认，真车复核项**。

### 7.5 /api/state 字段口径（JWebService.cpp:55-72、131-401）

| 字段 | 格式 | 说明 |
|---|---|---|
| name / ip / robot_type / map_name | string | 基本信息 |
| mode | string | JMode 名（来自 buffer robot info） |
| status | string | `"mode,子状态"` 拼接（:154） |
| vel | string | `"%.02lf,%.02lf,%.02lf"` = v,w,vy（:84/156） |
| pose | string | `"x,y,th"`（th 弧度；长度单位依车型参数，毫米口径待真车复核） |
| score | int | 定位评分 |
| battery | int | 百分比 |
| charing | bool | 充电中（**原文如此，非拼写错误**；:160 `mRobot->IsCharged()`） |
| ctrl_mode | int | 当前恒 0（:148/161） |
| safe / motor | bool | 安全模式 / 电机使能 |
| current_routes | object | 见 7.3；status 恒 "running" |
| input / output / virtual | int 数组 | IO 位（bitset 反转后逐位 0/1；:174-220） |
| laser_data / path_points / clearances / robot_size | object | 高频画布数据 |
| fork_info | object | 仅 robot_type 含 "flt"：`fork_height`（当前叉高，源自 `liftHeight`，毫米口径）、`fork_up`（=robot.Max_ForkHeight）、`fork_down`=0、`flap`、`ctrl_flag`、`sub_type`（P1500/P2000=1、A1500=0、R1500=2；R1500 另有 `fork_max_dis`/`fork_cur_dis`）（:314-365） |
| alarm | string | `normal/estop/lost/stuck`：电机未使能→estop；status 含 "lost"→lost；flt 车型 TruckState Front/Back/Side/Left/RightTruck→stuck、EStop→estop；其余 status 含 brake/stuck→stuck（:367-401） |

### 7.6 WS /ws/high 与 /ws/low（JWebHttpServer.cpp:72-76、275-305）

- 内嵌 CivetWeb 推送，文本帧 JSON，入站数据忽略（:244-252）。
- `/ws/high`（默认 10Hz）：`{type:"high", vel, pose, laser_data, path_points, clearances, robot_size}`（BuildHighFreqState，:74-91）。
- `/ws/low`（默认 1Hz）：`{type:"low", 基础状态 + current_routes + IO + fork_info + alarm}`（BuildLowFreqState，:93-106）。
- 频率由 `Start(port, highHz, lowHz)` 参数决定（:22-29）。

### 7.7 协议风险与待复核清单

1. **P1**：route 完成/失败语义未经真车复核（`_judge_node` 复合判定 + follow_back/get_pallet 无物理量）。
2. `head` cmd 的 JMode 处理者与参数（angle/speed/steer_angle/use_pid）源码未确认。
3. 长度/速度单位（pose、vel、fork_height）毫米/毫米每秒口径依车型参数，真车核对。
4. `succeed:true` 只表示受理；失败路径（ActiveMode 拒绝、mode 内部失败）的 HTTP 层不可见，只能靠 state 轮询。
5. 真车 404 的 `schedulerthis` 等废弃端点不得使用。

## 8. 安全设计

### 8.1 分级锁定矩阵

| 指令类别 | 配对 | 现场锁 | 唤醒武装（cabin） | 任务流运行中 |
|---|---|---|---|---|
| STOP / QUERY_* / CONFIRM / CANCEL | 需 | 否 | 否 | 放行 |
| drive / TURN（点动调速） | 需 | **需** | 需 | 拒 `flow_running` |
| FORK（货叉） | 需 | **需** | 需 | 拒；幅度>100mm 另需确认 |
| TASK_*（head/follow_back/get_pallet/charge） | 需 | **需** | 需 | 拒 |
| FLOW_*（任务流启动/暂停/继续/取消） | 需 | **否**（有意分级，R-06） | 需 | start 409；pause/resume/cancel 放行 |

### 8.2 确定性保护层

| 机制 | 实现 | 触发与行为 |
|---|---|---|
| 运动看门狗 | `safety/watchdog.py` | drive 后 2s（可配）无续令自动 stop + 广播 watchdog_stop |
| 急停联动 | `safety/alarm_monitor.py` | estop 上升沿强制退出现场锁 + TTS 告警；恢复需重新解锁 |
| 低电保护 | `taskflow/engine.py` | <20% 暂停任务流 + 自动下 charge；≥80% 只广播不自动恢复 |
| 参数确认 | `tasks/dialogue.py` | 安全关键参数缺省追问；follow_back 汇总确认；货叉大幅确认 |
| 意图白名单 | `nlu/router.py` | LLM 输出 INTENT_NAMES + slot 类型校验，不过则丢弃 |
| 任务流互斥 | `taskflow/engine.py` | 全局单流；运动指令拒行；STOP 始终可达 |
| 节点超时 | `taskflow/engine.py` | 默认 120s（可配）超时停车判 failed |

### 8.3 降级矩阵

| 故障 | 降级行为 |
|---|---|
| 云端 ASR 超时/HTTP 失败 | 播 `fail_asr`（失败：识别失败），不回退 sherpa |
| 云端 ASR 空音频/空识别 | 静默忽略，不播报、不执行 |
| LLM 不可达 | 纯规则 NLU；复合指令只执行首个规则命中；打一次 warning 后静默 |
| 云端 TTS 不可达 | 超时后 piper 兜底；piper 也不可用则 `engine:"mock"` audio=None → 前端 speechSynthesis |
| piper 不可用 | 云端/缓存命中仍可播；均失败则 TTS 回退 `engine:"mock"` audio=None → 前端 speechSynthesis |
| ASR/LLM key 缺失 | 识别失败或 LLM 降级纯规则；文本链路仍可用 |
| jarvis 不可达 | 任务流重试 3 次 + 续跑窗口 ≈5s + lost/back 广播；急停监控静默跳过 |
| core 重启 | 任务流快照恢复 paused；配对/现场锁/武装失效需重新配对解锁；ASR/LLM 所选模型从 runtime_models.yaml 恢复 |

### 8.4 隐私与网络安全

- ASR/NLU/TTS 联网增强；规则 NLU 与 piper 仍可离线；日志记文本不录音频，7 天删除。
- 车端接口无鉴权；forkai-core Bearer + 配对码 + 现场锁构成全部访问控制——**部署前提是厂内受信网络**；CORS 全开同样基于此假设。

## 9. 数据与持久化

| 数据 | 位置 | 生命周期 |
|---|---|---|
| 任务流定义 | `services/core/data/flows/{id}.json`（每流一文件 + 内存索引） | 持久，用户 CRUD |
| 引擎快照 | `data/flows/.engine_state.json` | running/paused 迁移落盘；终态清除；启动恢复为 paused |
| 话术配置 | `config/utterances.zh-CN.json` | 启动加载 |
| 核心配置 | `config/core.config.yaml` + `config/runtime_models.yaml` + env 覆盖 | 启动加载；模型选择运行期写回 runtime_models |
| 配对/现场锁/武装/ParamDialogue | 内存（SessionManager / ParamDialogue） | **重启即失**（R-03） |
| 模型资产 | `models/asr`、`models/llm`、`models/piper`、`data/tts_cache/` | 部署时安装；TTS 缓存运行期生成 |
| 日志 | 文本日志（不录音频），7 天删除 | 滚动 |

## 10. 部署架构

- **目标**：RK3588 / 8 GB / Ubuntu 20.04；开发验证机 x86_64。
- **install.sh**（382 行）：架构检查（x86_64/aarch64，拒绝 armv7l）→ Node20 → Miniconda+venv+pip（阿里云镜像）→ npm install + 前端构建 → piper + 模型 → ASR 模型 + 热词 → Qwen2 GGUF + **源码编译 llama.cpp b10256**（RK3588 约 15-25 分钟）→ 写 core.config.yaml → systemd 双单元 enable --now；支持 `$INSTALL_DIR/.offline-assets/` 离线包；**自动停用并删除 V1 旧单元**。
- **systemd**：`forkai-core.service`（Restart=always，MemoryMax=4G）、`forkai-llm.service`（MemoryMax=2G，warmup 常驻）；`forkai-gateway/speech.service` 为 V1 遗留仅历史参考。
- **x86 与 RK3588 差异**：llama.cpp 需 aarch64 重新编译；sherpa-onnx int8 模型 ARM RTF 待复核；车载 ALSA 麦与扬声器链路未实测。

## 11. 质量属性与验证矩阵

| 验证资产 | 覆盖 | 前置条件 | 证据等级 |
|---|---|---|---|
| `scripts/nlu_corpus_test.py` | NLU 黄金语料 75 条（规则层 + 纠偏） | 无 | E1 |
| `scripts/nlu_llm_test.py` | 混合路由 10 条（5 规则 + 5 LLM） | 云端 LLM key | E1（含人工评估） |
| `scripts/asr_cloud_test.py` | 云端 ASR 出字 + 不可达失败 | SiliconFlow key | E2 |
| `scripts/asr_offline_test.py` / `asr_noise_test.py` | piper 合成 → sherpa 识别；噪声 SNR 0~20dB | 模型 | E1 |
| `scripts/ws_audio_test.py` | /ws/audio 全链路 | mock+core | E2 |
| `scripts/protocol_conformance.py` | scheduler 契约 4 项 | mock+core | E2 |
| `scripts/week2_regression.py` | 六任务/追问/复合/问答/提示音/免唤醒/LLM 降级/前端构建 | mock+core+llama | E2（会 pkill llama） |
| `scripts/week3_engine_test.py` | 引擎状态机/超时/暂停继续/取消/低电/分级锁定 | mock+core+llama | E2 |
| `scripts/week4_voice_flow_test.py` | 语音触发/模糊匹配/断网续跑/急停/快照/配对码 | mock+core+llama | E2（会重启 mock 与 core） |
| `scripts/soak_test.py` | 随机指令长跑 + 故障注入 | mock+core | E2 |
| `tests/e2e`（6 spec，Playwright，Node≥20） | 配对/现场锁/文本语音/PTT 链路/流程编辑器/急停 UI | 前端已 build + mock+core | E3（04 不验识别文本；05 标注未完整验证） |
| 前端 typecheck + build | `npm run typecheck/build -w @forkai/web`（**不用根 build**，含冻结 V1） | — | E0 |

变更验证矩阵遵循仓库根 AGENTS.md；跳过项必须说明理由；失败测试必须记录，未执行标 `未验证`。

## 12. 已知缺口与技术债

| 编号 | 项 | 位置 | 说明 |
|---|---|---|---|
| G-01 | 完成判定注释陈旧 | `taskflow/engine.py:9` | 仍写 `_is_node_done`，实际 `_judge_node`/`_physical_verdict` |
| G-02 | follow_back/get_pallet 无物理判定 | `engine.py:490` 注释明示 | 只按 route 消失判成功（R-01） |
| G-03 | prompts.py:5 注释陈旧 | `nlu/prompts.py:5` | "TASK_* 为 Week 2 预留" 实际已全部实现 |
| G-04 | 任务流 start 不要求现场锁 | `routes_flows.py:5` | 有意分级；真车决策点（R-06） |
| G-05 | 编辑器不支持并行组/options | `FlowEditor.vue:282-287` | 保存丢 parallel_groups（R-05） |
| G-06 | packages/shared 滞后无人引用 | `packages/shared/src/index.ts` | 意图/事件类型过期（R-07） |
| G-07 | 会话全内存 | `session/manager.py`、`tasks/dialogue.py` | 重启失效（R-03） |
| G-08 | mock 缺 autodrive | `mock-jarvis/src/index.js:237` | 只验链路（R-04） |
| G-09 | e2e 04 不验识别结果 | `tests/e2e/specs/04*` | 假麦静音；识别靠服务端合成音脚本 |
| G-10 | llama-server 二进制版本未标注 | `services/llm-sidecar/bin/` | deploy 用 b10256 源码编译 |
| G-11 | `head` cmd 车端处理者未确认 | jarvis-fork src 未检索到 | 可能在预编译库；真车复核 |
| G-12 | 文档不一致 6 项 | 见 `docs/prd.md` §9.2 | D-01~D-06，登记未修 |
