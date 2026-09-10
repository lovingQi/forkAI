# forkAI 架构设计 V2

- 版本：V2
- 日期：2026-08-04
- 对应代码状态：services/core Week 1-4 完成（回归全绿）

## 1. 总体架构

```
┌─────────────────────────────┐
│  浏览器前端 apps/web          │  车载麦克风（cabin_listen，默认关）
│  （语音/编辑器/监控）          │        │
└──────┬──────────────────────┘        │ PCM16
       │ HTTP/WS                       ▼
       ▼                       ┌────────────────┐
┌──────────────────────────────────────────────┐
│  forkai-core（FastAPI，:19000）               │
│  ├─ api/        REST + WS 路由                │
│  ├─ asr/        sherpa-onnx 流式识别           │
│  ├─ nlu/        规则 → LLM → UNKNOWN 路由      │
│  ├─ executor    意图执行 + 分级锁定             │
│  ├─ tasks/      参数schema + ParamDialogue    │
│  ├─ taskflow/   任务流引擎（校验/存储/执行/匹配）│
│  ├─ safety/     点动看门狗 + 急停监视          │
│  ├─ session/    配对/现场锁/唤醒武装           │
│  ├─ tts/        缓存 → CosyVoice2 → piper → mock │
│  └─ jarvis/     车端 HTTP 客户端 + route 构造  │
└──────┬───────────────────────┬───────────────┘
       │ HTTP /api/* + WS      │ OpenAI 兼容
       ▼                       ▼
┌──────────────┐      ┌─────────────────┐
│ jarvis 车端   │      │ llama-server     │
│ （:8080）     │      │ Qwen2-0.5B :19002│
└──────────────┘      └─────────────────┘
前端静态托管：core 直接挂载 apps/web/dist（存在时）。
```

## 2. 模块职责表

| 模块 | 文件 | 职责 |
|------|------|------|
| asr.engine | app/asr/engine.py | SherpaASR 单例懒加载，推理解放线程池，热词加载 |
| asr.stream | app/asr/stream.py | 每路音频的 partial/final 状态机，flush 尾 padding |
| asr.capture | app/asr/capture.py | 车载常听采集（sounddevice，默认关闭，失败只告警） |
| nlu.rules | app/nlu/rules.py | 正则意图全表 + 唤醒词同音字模糊匹配 + ASR 纠偏表 |
| nlu.llm | app/nlu/llm.py | llama-server 客户端，JSON 围栏剥离+结构校验，静默降级 |
| nlu.router | app/nlu/router.py | 规则→LLM→UNKNOWN；规则命中覆盖率判断分流复合指令 |
| nlu.prompts | app/nlu/prompts.py | LLM 意图白名单 + system prompt（few-shot） |
| executor | app/executor.py | 意图执行：点动/货叉/任务/查询/流控 + 对话整合 + 分级锁定 |
| tasks.schemas | app/tasks/schemas.py | TASK_* 参数 schema（required/safety/default/追问话术/确认策略） |
| tasks.dialogue | app/tasks/dialogue.py | ParamDialogue 追问/确认/取消状态机（30s 惰性超时） |
| tasks.pending | app/tasks/pending.py | 早期挂起存储（已被 dialogue 接管，保留） |
| taskflow.schema | app/taskflow/schema.py | FlowJSON 校验 + 并行组缩点/判环/入口计算 |
| taskflow.store | app/taskflow/store.py | 流程 JSON 持久化 + 中文名索引 |
| taskflow.engine | app/taskflow/engine.py | FlowEngine 状态机/pause/resume/cancel/断网续跑/低电量/快照 |
| taskflow.matcher | app/taskflow/matcher.py | 流程名模糊匹配（编辑距离，阈值 0.6） |
| safety.watchdog | app/safety/watchdog.py | 点动看门狗（默认 2s 无指令自动停） |
| safety.alarm_monitor | app/safety/alarm_monitor.py | 急停上升沿强制退出现场锁 |
| jarvis.client | app/jarvis/client.py | 车端 HTTP 客户端（state/map/params/control/start_route） |
| jarvis.routes_builder | app/jarvis/routes_builder.py | 五种任务 route 节点构造，缺参抛 ValueError |
| tts.service | app/tts/service.py | 合成入口：缓存 → 云端 → piper → mock |
| tts.cloud | app/tts/cloud.py | SiliconFlow CosyVoice2 客户端 + WAV 头修正 |
| tts.cache | app/tts/cache.py | 按文本哈希落盘；超限按 mtime 淘汰 |
| tts.prewarm | app/tts/prewarm.py | 启动后台预热固定话术与可枚举参数 |
| tts.piper | app/tts/piper.py | piper CLI 合成（拉丁字母转写）+ mock 回退 |
| session.manager | app/session/manager.py | 配对码/Token/现场锁/唤醒武装（TTL 管理） |
| events | app/events.py | /ws/events 客户端集合 + broadcast |
| api | app/api/*.py | REST/WS 端点（见 api-v2.md） |

## 3. 关键链路时序

### 3.1 文本语音指令（/api/voice/text）

```
前端 → POST /api/voice/text {text,channel}
core → auth(Bearer) → cabin 通道先匹配唤醒词（命中→arm_wake+TTS"在"+广播，纯唤醒词直接返回）
    → correct_asr 纠偏 → router.parse_intent → [意图列表]
    → 逐个 executor.handle（锁定检查/对话/执行）→ render 话术 → TTS（缓存/云端/piper）
    → broadcast intent + tts（每意图一轮）
    → 返回 {succeed,intent,utterance,audioBase64,target,intents[]}
```

### 3.2 WS 音频链路（/ws/audio）

```
前端 → WS 连接 → 首帧 JSON {pairToken,channel}（失败 4401 关闭）
     → 二进制帧 PCM16 16kHz mono → ASRStream.feed → 回发 {"type":"partial",text}
     → {"event":"end"} 或端点检测 → flush 出 final
     → broadcast asr_final（前端停播 TTS）→ run_utterance（同 3.1）
     → 回发 {"type":"final",text,...run_utterance 结果}；PTT 一次 final 后重置流
```

### 3.3 任务流执行链路

```
编辑器/语音 → POST /api/flows/{id}/start（或 FLOW_START 确认后）
engine → 全局互斥检查 → 逐单元执行：
  任务节点 → build_route → start_route（重试3次）→ 每 500ms 轮询 get_state
    → _is_node_done 判完成（超时 node_timeout_s 判 failed）
  drive 节点 → control(drive) → 定时 → control(stop)
  succeeded → success 出边；failed → fail 出边（无则流 failed）
  每次状态变化 broadcast flow_event；暂停/取消即时响应
```

### 3.4 追问确认链路（ParamDialogue）

```
TASK_* 缺 required 参数 → 挂起会话(stage=collect) → 追问第一参
用户回答（任意文本，优先喂对话，UNKNOWN 也消费）→ 提取参数 → 下一问/集齐
集齐 → stage=confirm → "确认执行：{summary}吗"
"确认" → 执行（fork/task 需现场锁；flow 不需要）；"取消" → 放弃
30s 无应答 → 超时清理，晚到确认回 "已超时，任务取消"
```

## 4. route 完成判定语义（真车待校准）

- **唯一判定函数：`FlowEngine._is_node_done(state, route_name)`**（engine.py）。
  当前为 mock 语义：`state.current_routes.routes == route_name and status == "finished"`。
- jarvis 真车 HTTP 映射：`POST /api/control/scheduler` 内联 route（源码已确认：JWebHttpServer.cpp:62 注册 → JWebService::SchedulerThis，JWebService.cpp:786-793）；body 为 `{"name":..., "content":{"a":{cmd,...}}}`（节点表在 content 键下）。备选 `/api/control/routes` 命名路线。真车对接时只改 `_is_node_done` 与 `JarvisClient.start_route` 两处。

## 5. 模型清单

| 模型 | 位置 | 大小 | 实测表现 |
|------|------|------|----------|
| sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23（int8） | services/core/models/asr/ | 78M | RTF≈0.06 @x86 num_threads=1（2线程非确定）；指令词热词加权；"电量"等同音词靠纠偏表 |
| Qwen2-0.5B-Instruct Q4_K_M | services/core/models/llm/ | 380M | 格式守法；复合指令偶有漏意图/单位换算错误；靠白名单校验+规则数值覆盖+部分规则回退兜底；保留升 1.5B 选项 |
| piper zh_CN-huayan-medium | services/core/models/piper/ | 63M | RTF≈0.1；云端不可达时兜底 |
| CosyVoice2-0.5B（SiliconFlow） | 云端 API | — | 非流式短句中位约 700ms；WAV 24kHz；本地缓存命中后零合成延迟 |

llama.cpp 为 b10256 源码编译（-DGGML_NATIVE=OFF -DLLAMA_CURL=OFF，gcc 9.4），二进制在 services/llm-sidecar/bin/。

## 6. 与 TS 旧架构对应关系

| V1（冻结） | V2 |
|------------|-----|
| voice-gateway src/index.ts | app/main.py + app/api/* |
| voice-gateway src/session.ts | app/session/manager.py |
| voice-gateway src/watchdog.ts | app/safety/watchdog.py |
| voice-gateway src/jarvis.ts | app/jarvis/client.py |
| voice-gateway src/executor.ts | app/executor.py |
| voice-gateway src/intent.ts | app/nlu/rules.py |
| voice-gateway src/speak.ts | app/speak.py（渲染/路由）+ app/tts/service.py（合成入口） |
| voice-gateway config/gateway.config.yaml | config/core.config.yaml |
| voice-gateway config/utterances.zh-CN.json | config/utterances.zh-CN.json（扩充） |
| services/speech（TTS HTTP 服务） | app/tts/service.py（云端+缓存）+ app/tts/piper.py（core 内 piper CLI 兜底） |
| packages/shared 类型 | 继续为前端共享类型源（IntentName 同步扩充） |
