# forkAI 产品需求文档（PRD）

| 项 | 内容 |
|---|---|
| 版本 | 1.0 |
| 日期 | 2026-08-11 |
| 状态 | 整合重述稿 |
| 基线声明 | 本文档是对现有需求与当前实现的整合重述，**需求事实来源仍为 `docs/requirements-v2.md`**（V1 历史见 `docs/requirements-v1.md`）。本文不取代基线；如有冲突以基线为准。 |
| 关联文档 | 架构 `docs/architecture-v2.md`、实施 `docs/implementation-plan-v2.md`、验收 `docs/acceptance-v2.md`、API `docs/api-v2.md`、技术设计 `docs/tdd.md` |

## 1. 产品概述

forkAI 是部署在叉车车载工控机上的**全离线中文语音控制与任务流编排系统**。操作员通过浏览器（车载屏/手机/电脑）或车载麦克风，用中文语音完成叉车点动、调速、货叉升降、原地旋转、点到点搬运、栈板识别取货、自动充电等六类任务，并可通过可视化流程编辑器编排多节点任务流后语音触发执行。

系统形态：

```
浏览器/车载麦克风（apps/web）
    ↓ HTTP/WebSocket
forkai-core（FastAPI 唯一后端：ASR/NLU/TTS/任务流引擎/安全）
    ↓ HTTP/WebSocket                ↓ HTTP（OpenAI 兼容）
jarvis 车端（真实车辆程序）      llama-server（NLU 兜底侧车）
```

核心价值：

- **全离线**：ASR/NLU/LLM/TTS 全部本地运行，适应厂区弱网/无网环境。
- **中文语音优先**：针对叉车场景词汇（站点名、货叉、充电）做 ASR 纠偏与唤醒词同音字容错。
- **确定性安全层**：概率模型（LLM）不直接控制车辆，所有运动指令经过规则校验、分级锁定、看门狗等确定性保护。
- **任务流编排**：六类原子任务可组合为带分支、并行组的多节点流程，语音一句话触发。

## 2. 目标与非目标

### 2.1 产品目标

- 现场操作员近场语音点动叉车（一号场景），扫码现场解锁后使用。
- 语音完成六类任务的参数化下发与确认。
- 可视化编排任务流并语音触发，执行过程可暂停/继续/取消。
- 车况只读问答与告警处置建议。
- 远程端（手机/电脑浏览器）分级权限：可任务、可问答，**不可点动**。
- 目标硬件 RK3588（ARM）/ 8 GB / Ubuntu 20.04 上全离线运行。

### 2.2 非目标（范围外）

- **V1 技术栈全线冻结**：`services/voice-gateway`、`services/speech`、forkweb 不再演进、不启动、不修改。
- **智能绕障**：由车端 jarvis 程序处理，forkAI 不实现绕障逻辑。
- **远程点动**：远程端不可点动（分级权限决策）。
- **语音 STOP 不等于实体急停**：语音"停止"是软件停止指令，不替代物理急停按钮。
- **激光/避障画面不做进 UI 监控主界面**以外的定制展示（V1 决策）。
- **仅支持中文普通话**；不录制、不存储音频。
- forkweb 后续删改不在本项目范围。

> 注意：V1 曾决策"不做货叉升降（可查叉高）"，V2 已反转为完整货叉升降控制。引用 V1 文档时注意该条已过时。

## 3. 用户与场景

### 3.1 用户角色

| 角色 | 接入方式 | 权限 |
|---|---|---|
| 现场操作员（主） | 车载屏/手机扫码，浏览器打开 `http://<车IP>:19000/` | 全部功能：点动、六任务、任务流、问答、流程编辑 |
| 现场操作员（车载麦） | 车载常听麦克风（cabin 通道） | 唤醒词武装后同现场权限；武装窗口 30s |
| 远程用户 | 手机/电脑浏览器经厂内 WiFi 访问车 IP | 配对后可任务/问答，**不可点动**（无现场锁） |

### 3.2 核心场景

1. **近场点动（一号场景）**：扫码 → 一次性码现场解锁 → PTT 或唤醒词 → "前进/后退/左转/停" → 看门狗 2s 无续令自动停车。
2. **语音任务**：解锁后 "升起货叉到150毫米" / "原地旋转90度" / "去A点取货" / "回充电桩充电" → 缺安全关键参数时语音追问 → 执行并播报结果。
3. **任务流执行**：流程编辑器编排"取货→送到→放下→回充"→ 语音 "执行XX流程" → 模糊匹配确认 → 逐节点执行，可语音暂停/继续/取消。
4. **车况问答**："电量多少/现在什么模式/有没有告警" → TTS 播报；告警时给出处置建议。
5. **异常处置**：任务流执行中断网自动续跑；电量 <20% 自动暂停并下充电路线；实体急停触发后强制退出现场锁，恢复需重新解锁。

## 4. 功能需求

> 每条 FR 标注来源出处与验收要点；实现与验证状态统一见第 8 章。

### FR-01 语音交互链路

- **描述**：语音/文本输入 → sherpa-onnx 流式 ASR（端点检测 + PTT 手动结束）→ ASR 同音误识别纠偏 → 混合 NLU（规则正则优先 → Qwen2-0.5B LLM 兜底；复合指令拆分最多 3 个动作；意图白名单校验；LLM 不可达自动降级纯规则）→ 执行 → piper TTS 播报（失败回退浏览器 speechSynthesis）。
- **来源**：requirements-v2.md §2.1；实现 `services/core/app/asr/`、`app/nlu/`、`app/tts/`、`app/api/routes_voice.py`。
- **验收要点**：x86 RTF<1；LLM 不可达时规则链路不受影响；纠偏表生效；文本链路 `/api/voice/text` 与音频链路 `/ws/audio` 结果一致。

### FR-02 唤醒与连续对话

- **描述**：可配置唤醒词（默认"玖物玖物"），同音字等价组模糊匹配（玖/九/酒/久 × 物/屋/乌/五/务/舞/武）；cabin 通道唤醒后武装 30s 免唤醒窗口，每次成功交互滚动续期；纯唤醒词只应声不触发 LLM。
- **来源**：requirements-v2.md §2.6；实现 `app/nlu/rules.py`（唤醒匹配）、`app/session/manager.py`（武装窗口）、`app/api/routes_voice.py`。
- **验收要点**：同音字可唤醒；30s 窗口滚动续期有回归断言；武装状态广播 `wake_armed`。

### FR-03 六任务语音控制

- **描述**：

| 任务 | 说明 | 关键参数 |
|---|---|---|
| `drive` 点动 | 前后左右停 + 调速；看门狗 2s 无续令自动停 | 方向、速度档（默认 20/40，步进 5，可配） |
| `head` 原地旋转 | 指定角度（"掉头"=180°） | angle（度，可负）、speed |
| `focklift` 货叉升降 | 默认范围 75~210mm（可配）；\|目标-当前\|>100mm 需确认 | pos（mm） |
| `follow_back` 点到点/盲叉 | 起点到终点搬运，可选取货 | start_name、target_name、get_pallet |
| `get_pallet` 栈板识别取货 | 相机识别栈板 | 观察/取货叉高等（有默认值） |
| `charge` 自动充电 | 回充电桩 | goal（桩名或 auto） |

- **来源**：requirements-v2.md §2.2；实现 `app/tasks/schemas.py`、`app/executor.py`、`app/jarvis/routes_builder.py`。
- **验收要点**：六任务触发词与 route 构造有黄金语料与协议符合性断言；货叉确认阈值可配。

### FR-04 参数追问与确认对话

- **描述**：安全关键参数缺省时语音追问（ParamDialogue，30s 惰性超时 + 120s 硬 TTL）；可选参数用默认值；任意轮可"取消"；"停止"放弃并停车；`follow_back` 参数集齐后必须汇总确认；confirm 阶段收到新指令放弃旧确认按新指令处理。
- **来源**：requirements-v2.md §2.2；实现 `app/tasks/dialogue.py`、`app/tasks/schemas.py`。
- **验收要点**：追问-补齐-执行全链路回归；超时回 `timeout_cancel` 话术。

### FR-05 任务流编排与执行

- **描述**：
  - 编辑器：`/#/flow` Vue Flow 流程图编辑器，六类节点、参数动态表单（按 `/api/tasks/schemas` 渲染，required/safety 标记）、success/fail 分支连线（每节点每类出边限 1 条）、中文名、布局持久化、执行高亮。
  - 引擎：全局同一时刻仅一条流 running/paused；六态状态机 idle/running/paused/succeeded/failed/cancelled；节点态 pending/running/paused/succeeded/failed/skipped；暂停=立即停车挂起、继续=当前节点从头重发、取消=剩余 skipped；并行组结构支持（asyncio.gather，全成才出组）。
  - 语音触发："执行XX流程" 模糊匹配（包含 0.8 / Levenshtein ratio，阈值 0.6，top1-top2 分差 <0.15 判歧义拒答）→ TTS 二次确认 → 执行。
  - 任务流启动本身**不要求现场锁**（有意分级；真车可一处改动加强）。
- **来源**：requirements-v2.md §2.3、§2.5；实现 `app/taskflow/`、`apps/web/src/views/FlowEditor.vue`。
- **验收要点**：week3 7 项引擎回归 + week4 语音触发回归；FlowJSON 校验（入口唯一、无环、并行组不重叠）。

### FR-06 车况问答与告警解释

- **描述**：固定查询意图——电量、模式、位置、叉高、电机、速度、当前任务、综合车况（QUERY_STATUS）、告警；告警解释答案库（estop/lost/stuck/normal 内置处置建议）。只读，不改变车辆状态。
- **来源**：requirements-v2.md §2.4 + 后续提交 e6011ae（QUERY_STATUS）；实现 `app/executor.py` QUERY_* 分支。
- **验收要点**：问答三连回归；告警解释话术按当前 alarm 状态选择。

### FR-07 配对、现场锁与分级锁定

- **描述**：
  - 配对：6 位数字配对码，TTL 5min、一次性；确认后签发 pairToken（TTL 24h）；除 `/api/health` 与 `/api/pair/*` 外全部接口 Bearer 鉴权，未配对 401 `unpaired`。
  - 现场锁：车载屏/二维码显示一次性现场码（nonce/code 每次解锁后轮换），解锁获得现场锁，TTL 45min；支持 force 抢占（stolen 标记）；`POST /api/site/end` 释放。
  - 分级锁定：点动/货叉/TASK_* 需现场锁；cabin 通道另需唤醒武装；任务流 running/paused 时拒绝运动级指令（`fail_flow_running`），STOP/查询/确认/取消始终放行。
- **来源**：requirements-v2.md §2.5；实现 `app/api/routes_pair.py`、`routes_site.py`、`app/session/manager.py`、`app/executor.py`。
- **验收要点**：配对码一次性 + 现场码轮换有断言；409 held→force 抢占 E2E；403 no_site 拦截。

### FR-08 语音体验

- **描述**：冷静女声、普通话、少情绪、偏快；成功/失败提示音（880Hz 120ms / 220Hz 300ms，可关）；TTS 可打断（新识别 final 即停当前播报）；TTS 前导静音垫避免吃字；拉丁字母转中文读音（站点名保真）。
- **来源**：requirements-v2.md §2.6、requirements-v1.md 语音体验表；实现 `app/tts/piper.py`、`app/speak.py`、`apps/web/src/stores/session.ts`。
- **验收要点**：提示音样本级拼接断言；打断链路 E2E；话术配置 `config/utterances.zh-CN.json`（52 键）与文档同步（当前存在缺口，见第 9 章）。

### FR-09 异常处理与故障韧性

- **描述**：
  - 断网续跑：节点轮询连续失败 10 次（≈5s）才判失败；断连期间 2s 节流广播 `flow_jarvis_lost`；恢复后广播 `flow_jarvis_back` 并重发当前节点；下发重试 3 次间隔 1s。
  - 崩溃恢复：引擎状态快照落盘（`data/flows/.engine_state.json`），core 重启后恢复为 paused 等人工 resume。
  - 低电量：<20% 自动暂停任务流 + 播报 + 自动下发充电路线；充到 ≥80% 广播但**不自动恢复**（防无人值守自启动）。阈值均可配。
  - 急停：alarm=estop 上升沿强制退出现场锁 + TTS 告警播报；恢复需重新现场解锁；可用配置关闭该行为。
- **来源**：requirements-v2.md §2.7；实现 `app/taskflow/engine.py`、`app/safety/alarm_monitor.py`。
- **验收要点**：week4 断网/急停/快照恢复回归；低电暂停+充电回归。

### FR-10 监控与地图 UI

- **描述**：Dashboard 左地图画布（激光、位姿、路径、车体轮廓、右键"到达"下发 autodrive），右状态面板（车况/实时数据/叉车信息/IO 位）；头部常驻车端连接/配对/现场剩余时间与全局"停"按钮；未配对显示配对门。
- **来源**：requirements-v1.md UI 决策 + 当前实现 `apps/web/src/views/Dashboard.vue`、`components/CanvasView.vue`。
- **验收要点**：E2E 配对/解锁/急停用例；车端断连 UI 状态可见。

## 5. 非功能需求

### 5.1 性能指标

| 指标 | 目标 | 当前实测/状态 |
|---|---|---|
| ASR 延迟 | <1s（RTF<1） | x86 实测 RTF≈0.06（14M int8，单线程）；**RK3588 待复核** |
| LLM 意图抽取 | <3s | 0.5B Q4_K_M 本地 <1s；超时 3s 可配 |
| TTS 合成 | — | piper RTF≈0.1（x86） |
| 任务成功率 | >95%（试点 7 天文本日志统计） | **试点未执行**；Mock 回归不构成试点数据 |
| 试点稳定性 | 连续 1 天运行 | **未执行** |
| 点动看门狗 | 默认 2.0s，可配 | 已实现，Mock 回归 |
| 现场锁 TTL | 45min | 已实现 |
| 配对码 TTL | 5min，一次性 | 已实现 |
| 唤醒武装窗口 | 30s 滚动续期 | 已实现 |
| 追问超时 | 30s 惰性 + 120s 硬 TTL | 已实现 |
| 低电阈值 | <20% 暂停充电、≥80% 广播（可配） | 已实现 |
| 断网判定 | 轮询 10 次×500ms≈5s；重试 3 次×1s | 已实现 |

### 5.2 离线、隐私与安全

- 全离线运行：所有模型本地部署，不依赖外网服务。
- 隐私：日志只记识别文本，**不录音频**；日志 7 天删除。
- 安全：概率模型不直接控车；运动指令必经分级锁定与看门狗；实体急停链路在车端，语音 STOP 不替代。

### 5.3 目标硬件与运行环境

- 目标：ARM RK3588 / 8 GB RAM / Ubuntu 20.04；开发验证机 x86_64 Ubuntu 20.04。
- 部署：`deploy/install.sh` 一键安装（含 llama.cpp 源码编译）+ systemd 双单元。
- 组件端口：forkai-core :19000（唯一后端与静态托管）、jarvis :8080、llama-server :19002（仅本机回环）、vite :5173（仅开发）。

## 6. 约束与依赖

| 依赖 | 说明 |
|---|---|
| jarvis 车端 | 真实协议以 `/home/xbl/Desktop/jarvis-fork` 源码与指定车型实测为准；开发用 `services/mock-jarvis` 模拟 |
| ASR 模型 | sherpa-onnx-streaming-zipformer-zh-14M int8（约 78M）+ 热词表 |
| LLM 模型 | Qwen2-0.5B-Instruct Q4_K_M（约 380M）；格式守法但不稳，保留升 1.5B 选项 |
| TTS 模型 | piper zh_CN-huayan-medium（约 63M）+ beep 提示音资产 |
| llama.cpp | b10256 源码编译（`-DGGML_NATIVE=OFF -DLLAMA_CURL=OFF`） |
| 前端运行时 | 浏览器（AudioWorklet 优先）；Playwright E2E 需 Node≥20 |

## 7. 验收标准

验收以 `docs/acceptance-v2.md` 与实际测试输出为准，证据分级（E0 文档/E1 单元/E2 Mock/E3 E2E/E4 HIL/E5 真车/E6 目标硬件）。不得以低等级证据冒充高等级验收。

| 验收域 | 入口 | 当前状态 |
|---|---|---|
| NLU 黄金语料（75 条） | `services/core/scripts/nlu_corpus_test.py` | 通过（E1） |
| LLM 直测 | `services/core/scripts/nlu_llm_test.py` | 可用（含人工评估项） |
| Week2 任务回归 9 项 | `scripts/week2_regression.py` | PASS（E2，2026-08-04/05） |
| Week3 引擎回归 7 项 | `scripts/week3_engine_test.py` | PASS（E2） |
| Week4 语音任务流回归 9 项 | `scripts/week4_voice_flow_test.py` | PASS（E2） |
| 协议符合性 4 项 | `scripts/protocol_conformance.py` | PASS（E2） |
| 前端 E2E 6 个 spec | `tests/e2e`（Node≥20） | 用例齐备；05 流程编辑器标注未完整验证（E3） |
| 真车联调（六任务+两条完整任务流） | plan 步骤 50 | **未执行（E5 缺）** |
| RK3588 部署/性能/耐久 | plan 步骤 58、acceptance §6 | **未执行（E6 缺）** |
| 试点 1 天压测（成功率>95%） | plan 步骤 51 | **未执行** |

## 8. 实施与验证状态（独立状态章）

> 本章集中标注实现/验证状态，需求正文不逐条标注。状态变化只需更新本章。

### 8.1 已实现且 Mock 环境回归通过（E2 级证据）

- Week 1：core 骨架、配对/现场锁、JarvisClient、看门狗、piper TTS、NLU 规则、闸门 A 回归、sherpa-onnx 接入（RTF≈0.06）、WS 音频链路、前端采集、货叉意图与 route。
- Week 2：llama-server 部署、LLM 三级路由、六任务 schema 与 route 构造、参数追问/汇总确认、问答扩充、TTS 打断/免唤醒/提示音。
- Week 3：FlowJSON 校验、FlowStore、FlowEngine 六态状态机 + 暂停/继续/取消 + 并行组、分级锁定、低电自动挂起充电、Vue Flow 编辑器。
- Week 4：流程名模糊匹配、语音触发任务流、断网续跑 + 快照 + 急停退出现场、全套文档、deploy/install.sh。
- 后续：真车 HTTP 映射与 `_judge_node` 复合完成判定（952a4dd）、ASR 纠偏增强（723cd7c）、QUERY_STATUS 综合车况（e6011ae）、前端 AudioContext 播放 + 前导静音（41f00f2）。

### 8.2 未闭环（需真车/真机/试点）

1. 真车 route 语义验证（plan 步骤 21，阻塞中）与六任务、两条完整任务流真车逐项验证（步骤 50）。
2. `POST /api/control/scheduler` 真车联调 + `_judge_node` 复合判定真车复核；follow_back/get_pallet 无物理量可判，目前只能按 route 消失判成功。
3. RK3588：sherpa-onnx aarch64 RTF/线程复核 + 车载麦克风实测；llama.cpp aarch64 编译与 0.5B/1.5B 选型复核。
4. 整车联调：站点名大小写口径、音区实测唤醒率、提示音音量/音色真机听感。
5. 试点 1 天压测（成功率 >95%、ASR <1s 统计）。
6. RK3588 干净环境离线部署演练 + 全流程冒烟（步骤 58）。
7. `parallel_groups` 真实并发路径与编辑器并行组 UI 未完整闭环；车载常听 cabin_listen 默认关闭未实测。
8. `docs/test-report.md` 尚不存在（真车联调后填写）。

### 8.3 证据边界声明

- 当前所有自动化结论来自 **x86 + Mock（E2）** 与浏览器 E2E（E3），不能推断真车（E5）或 RK3588（E6）行为。
- Mock 回归通过**不能**关闭真车 route、六任务、完整任务流、现场噪声、RK3588 性能、干净部署与耐久待办（AGENTS.md 红线）。

## 9. 遗留风险与文档不一致登记

### 9.1 产品/技术遗留风险

| 编号 | 风险 | 等级 | 关闭条件 |
|---|---|---|---|
| R-01 | 真车 route 完成语义未复核，follow_back/get_pallet 仅靠 route 消失判成功 | P1 | 真车联调（步骤 21/50） |
| R-02 | LLM 0.5B 格式守法但不稳 | P2 | 升级 1.5B 并复核（RK3588 选型） |
| R-03 | 会话（配对/现场锁/武装/对话）全内存态，core 重启即失 | P2 | 评估持久化或明确接受 |
| R-04 | mock-jarvis 无 `autodrive` 分支，画布"到达"只验链路 | P3 | mock 补 autodrive 模拟 |
| R-05 | FlowEditor 不支持编辑 parallel_groups/options，含并行组的流保存后丢失该字段 | P2 | 编辑器补并行组编排（V2.1 候选） |
| R-06 | 任务流启动不要求现场锁（有意分级，真车可能要求加强） | P2 | 真车决策点后落实 |
| R-07 | `packages/shared` 类型滞后且无人引用（V1 遗留） | P3 | 更新或移除 |
| R-08 | 提示音音量/音色未经真机听感验证 | P3 | 真车联调时验证（assets 可替换） |

### 9.2 文档不一致登记（本次仅登记不修复）

| 编号 | 不一致 | 事实 |
|---|---|---|
| D-01 | `architecture-v2.md` §3.3/§4、plan 步骤 37、`engine.py:9` 注释仍称完成判定为 `_is_node_done`（mock 语义） | 952a4dd 起已重构为 `_judge_node` + `_physical_verdict`（engine.py:434/470）复合判定 |
| D-02 | `docs/utterance-table.md` 仅 25 键 | 运行配置 `utterances.zh-CN.json` 实际 52 键，Week2–4 新增话术大多未入表 |
| D-03 | `docs/api-v2.md` §5 意图表 33 个 | 代码实际 34 个（e6011ae 新增 QUERY_STATUS） |
| D-04 | `acceptance-v2.md` §6 步骤编号与 plan 错位（ASR/LLM 真机项） | 对应 plan 步骤 13/23 |
| D-05 | `forkAI-文档1-整体业务流程.md` 多处失准（端口 5173 当入口、目录结构、纠偏方向写反、漏 head 触发词、残留对话语句） | 以代码与正式文档为准 |
| D-06 | `packages/shared` IntentName/GatewayEventType/TASK_INTENTS 与代码不符 | apps/web 与 mock-jarvis 均未引用该包 |

## 10. 需求追踪表

| FR | requirements-v2 | 主要代码 | 主要验证 |
|---|---|---|---|
| FR-01 | §2.1 | `app/asr/`、`app/nlu/`、`app/tts/`、`app/api/routes_voice.py` | nlu_corpus_test、nlu_llm_test、asr_offline/noise_test、ws_audio_test |
| FR-02 | §2.6 | `app/nlu/rules.py`、`app/session/manager.py` | week2 回归（滚动免唤醒项） |
| FR-03 | §2.2 | `app/tasks/schemas.py`、`app/executor.py`、`app/jarvis/routes_builder.py` | week2 回归、protocol_conformance |
| FR-04 | §2.2 | `app/tasks/dialogue.py` | week2 回归（追问/确认/超时） |
| FR-05 | §2.3 | `app/taskflow/`、`apps/web/src/views/FlowEditor.vue` | week3/week4 回归、e2e 05 |
| FR-06 | §2.4 | `app/executor.py`（QUERY_*） | week2 回归（问答三连） |
| FR-07 | §2.5 | `app/api/routes_pair.py`、`routes_site.py`、`app/session/manager.py` | week4 回归（配对码/轮换）、e2e 01/02 |
| FR-08 | §2.6 | `app/tts/piper.py`、`app/speak.py`、`apps/web/src/stores/session.ts` | week2 回归（提示音拼接/打断） |
| FR-09 | §2.7 | `app/taskflow/engine.py`、`app/safety/alarm_monitor.py` | week4 回归（断网/急停/快照）、soak_test |
| FR-10 | V1 UI + 实现 | `apps/web/src/views/Dashboard.vue`、`components/CanvasView.vue` | e2e 01/02/06 |
