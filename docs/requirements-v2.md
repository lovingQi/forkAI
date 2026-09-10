# forkAI 需求冻结 V2

- 版本：V2（冻结）
- 日期：2026-08-04
- 对应代码状态：services/core Week 1-4 全部实现并通过回归（week2 9/9、week3 7/7、week4 9/9）
- 基准：docs/implementation-plan-v2.md「已确认决策汇总」

## 1. 产品边界

forkAI 是叉车的全离线中文语音控制与任务流编排系统。V2 采用**方案 B：Python FastAPI 统一后端 forkai-core**（services/core，端口 19000），替代 V1 的 voice-gateway + speech 双服务。V1 两个服务冻结保留，不再演进。

系统组成：浏览器前端（apps/web）→ forkai-core（HTTP/WS、云端 ASR、NLU、TTS、任务流引擎）→ jarvis 车端（:8080）。llama-server 侧车保留但不作为运行时 NLU。

## 2. 功能需求

### 2.1 语音链路

- ASR：PTT 松手后整句上传 SiliconFlow `/audio/transcriptions`（默认 Qwen3-ASR-1.7B，界面可选当前列出的全部识别模型）；超时 5s 播「失败：识别失败」，不回退 sherpa。
- NLU：混合架构——规则正则优先；规则未命中或命中后残余文本仍含意图（复合指令）→ 云端大模型（默认 DeepSeek-V3.2，界面可选官方 DeepSeek-chat / V3 / Qwen3.5-27B / GLM-5.1）；LLM 输出经意图白名单校验，首个数值槽位以规则换算为准；LLM 不可达自动降级纯规则。
- TTS：云端 CosyVoice2 + 本地缓存，piper 兜底。

### 2.2 六任务

| 任务 | route cmd | 说明 |
|------|-----------|------|
| 点动 | drive | 前进/后退/转向/调速，走 MotionWatchdog（默认 2s 无指令自动停） |
| 原地旋转 | head | angle 度（可负，左正右负）/speed |
| 货叉升降 | focklift | pos(mm)/wait/tolerance，范围可配（默认 75~210） |
| 点到点/盲叉 | follow_back | start_name/target_name/get_pallet |
| 栈板识别取货 | get_pallet | 相机识别，全参数有默认 |
| 自动充电 | charge | goal（默认 auto）/side/angle/waitTime |

参数填入为混合模式：安全关键参数缺失时语音追问（ParamDialogue，30s 无应答超时放弃），可选参数用默认值；任何轮次可说"取消"放弃；follow_back 集齐后必须汇总确认。

### 2.3 任务流

- 编辑：Vue Flow 流程图编辑器（/#/flow），节点/连线/成功失败分支/参数表单/中文名/布局持久化（ui 字段）。
- 执行：后端 FlowEngine，全局单流互斥；状态机 idle/running/paused/succeeded/failed/cancelled；暂停=立即停车挂起、继续=当前节点从头重发、取消=剩余节点 skipped；success/fail 分支流转；并行组结构支持。
- 触发：语音"执行XX流程"，中文名精确→模糊匹配（阈值 0.6，top1-top2 分差<0.15 判歧义播报候选）+ TTS 二次确认。

### 2.4 智能问答

电量/模式/位置/叉高/电机/告警状态/速度/当前任务固定查询 + 告警解释答案库（estop/lost/stuck/normal 内置处置建议，未知原样播报）。

### 2.5 并发与锁定

- 分级锁定：点动/货叉/TASK_* 需持有现场锁；cabin 通道另需唤醒武装。任务流 running/paused 时运动级指令拒绝（fail_flow_running），STOP/查询/对话应答始终放行。任务流启动本身不要求现场锁（与点动分级）。
- 配对码一次性（TTL 5 分钟）、现场码每次解锁后轮换、现场锁 45 分钟 TTL。

### 2.6 语音体验

- 唤醒词可配置（默认"玖物玖物/玖物，玖物/九物九物"，ASR 同音字等价组模糊匹配）。车载常听（浏览器常听按钮、CabinListener、文本调试车载通道）已停用，语音入口为 PTT。
- TTS 可打断（识别出 final 指令广播 asr_final，前端停播）。
- 连续对话 30 秒免唤醒（wakeArmMs=30000，cabin 成功处理后滚动续期）。

### 2.7 异常处理

- 断网续跑：任务流节点轮询 get_state 连续失败 10 次（≈5s）才判节点失败，期间每 2s 广播 flow_jarvis_lost；恢复后广播 flow_jarvis_back 并重发当前节点；节点下发重试 3 次（间隔 1s）。
- 状态快照：运行/暂停状态落盘 data/flows/.engine_state.json，进程重启后恢复为 paused 等人工继续；终态清除。
- 低电量：<20%（可配）自动暂停任务流 + 播报 + 自动下充电路线（goal=auto）；充到 80%（可配）广播 flow_charge_full，默认不自动恢复。
- 急停：alarm 上升沿 estop → 强制退出现场锁 + 广播 + TTS 告警，恢复后需重新现场解锁（safety.estop_exit_site 可关）。

### 2.8 隐私与安全

ASR/NLU/TTS 联网增强（PTT 音频与意图抽取走云端，规则 NLU 与 piper 仍可离线）；出网内容为语音与模板话术文本；日志记录文本不录音频，7 天删除；配对/现场码见 2.5。所选 ASR/LLM 模型写入 `config/runtime_models.yaml`。

## 3. 硬件目标

ARM RK3588 / 8GB RAM / Ubuntu 20.04（开发验证机为 x86_64 Ubuntu 20.04；ARM 部署时需复核 piper 二进制与厂区出网）。

## 4. 验收标准（试点可用）

- 功能完整：本文件 §2 全部功能在 mock 环境自动回归通过。
- 性能：云端 ASR 超时 5s；LLM 意图抽取超时 10s；任务成功率 >95%（试点统计，方法见 acceptance-v2.md）。
- 真车关键功能验证：货叉/6任务/任务流的 jarvis route 语义（真车待办见 acceptance-v2.md §6）。

## 5. 与 V1 的关系

services/voice-gateway、services/speech 冻结于 V1 状态，仅作移植参照；forkweb 冻结不跟随更新。V2 全部新功能只在 services/core 线演进。
