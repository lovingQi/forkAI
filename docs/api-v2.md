# forkAI API 文档 V2

- 版本：V2
- 日期：2026-08-04
- 对应代码：services/core（app/api/*.py、app/main.py）
- 约定：除标注外全部端点需 `Authorization: Bearer {pairToken}`；未配对返回 `401 {"succeed":false,"error":"unpaired"}`。字段名 camelCase，与前端 apps/web 直接对接。

## 1. REST 端点

### 1.1 健康与配对（无需 auth）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/health | `{"ok":true,"vehicleId":"fork-01","watchdogMs":2000}` |
| POST | /api/pair/start | 生成一次性配对码（TTL 5min）→ `{"code":"469628","expiresAt":...}` |
| GET | /api/pair/pending | 当前待确认配对码或 `{"code":null,"expiresAt":0}` |
| POST | /api/pair/confirm | body `{"code":"..."}` → `{"succeed":true,"pairToken","expiresAt","clientId","vehicleId"}`；错误 `400 {"succeed":false,"error":"invalid_code"}` |

### 1.2 现场锁

| 方法 | 路径 | 请求体 | 响应/错误 |
|------|------|--------|-----------|
| GET | /api/site | — | `{"vehicleId","nonce","code","session":null\|{siteSessionId,holderClientId,expiresAt}}` |
| POST | /api/site/unlock | `{nonce?\|code?,force?}` | 成功 `{"succeed":true,"siteSessionId","expiresAt","holderClientId"}`（广播 site_changed）；`409 {"succeed":false,"error":"held","holderClientId"}`；`400 {"succeed":false,"error":"invalid"}` |
| POST | /api/site/end | `{force?}` | `{"succeed":true}`（广播 site_changed holderClientId:null）；`403 not_holder` |

### 1.3 语音

| 方法 | 路径 | 请求体 | 响应 |
|------|------|--------|------|
| POST | /api/voice/text | `{"text":"前进","channel":"ptt"\|"cabin"}` | `{"succeed":true,"intent":{name,slots,rawText},"utterance":"好的，前进","audioBase64":"...","target":"both","intents":[...]}`；空文本 `400 {"succeed":false,"error":"empty"}` |
| POST | /api/voice/stop | — | 等价于以 ptt 通道执行"停止" |

响应说明：`errorCode`/`audioBase64` 为空时省略该键；`intents` 为完整意图数组（复合指令多元素）；追问/待确认时 `utterance` 为追问或确认话术（如 `请告诉我起点`、`确认执行任务流取货演示流程吗`）。

### 1.4 车端代理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/state /api/map /api/params | 透传 jarvis；失败 `502 {"error":...}` |
| POST | /api/control/{action} | `drive` 需持现场锁（`403 no_site`）；`stop` 走 watchdog；其余透传 jarvis.control |

### 1.5 任务流

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/flows | `{"flows":[{"id","name","nodes"}]}` |
| POST | /api/flows | 创建，body=FlowJSON；校验失败 `400 {"succeed":false,"errors":[...]}`；成功 `{"succeed":true,"id"}` |
| GET/PUT/DELETE | /api/flows/{id} | 详情/更新（PUT 同样校验）/删除；不存在 `404 {"succeed":false,"error":"not_found"}` |
| POST | /api/flows/{id}/start | 启动（**不要求现场锁**，任务流与点动分级）；占用 `409 {"succeed":false,"error":"already_running","flowId"}` |
| POST | /api/flow-engine/pause | 暂停（立即停车）；非 running `409 not_running` |
| POST | /api/flow-engine/resume | 继续（当前节点从头重发）；非 paused `409 not_paused` |
| POST | /api/flow-engine/cancel | 取消（停车，剩余节点 skipped） |
| GET | /api/flow-engine/status | `{"flowId","flowStatus","currentNodeId","nodeStates":{...}}`；flowStatus ∈ idle/running/paused/succeeded/failed/cancelled；nodeStates ∈ pending/running/paused/succeeded/failed/skipped |
| GET | /api/tasks/schemas | 6 种节点参数元信息（按 route cmd 建键，见 §3） |

### 1.6 mock 调试（仅 services/mock-jarvis）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/debug/state | `{battery?,alarm?,charing?}` 直改内存状态（低电量/急停等联调用，非生产端点） |

## 2. WebSocket

### 2.1 /ws/audio（语音音频上行）

1. 连接后**第一条**为 JSON：`{"pairToken":"...","channel":"ptt"|"cabin"}`；校验失败以 code 4401 关闭。
2. 之后二进制帧 = PCM16 16kHz mono 音频块。
3. 下行 JSON 帧：
   - `{"type":"partial","text":"..."}` 实时部分识别
   - `{"type":"final","text":"前进","succeed":true,"intent":{...},"utterance":"...","audioBase64":"...","target":"...","intents":[...]}`（与 /api/voice/text 响应同构）
   - `{"type":"error","error":"asr_unavailable: ..."}`
4. 文本帧 `{"event":"end"}` = 手动结束（PTT 松开）→ flush 出 final；端点检测自动出 final。PTT 一次 final 后流重置可继续下一句；cabin 持续循环。

### 2.2 /ws/events（事件广播）

连接后可发 `{"type":"auth","pairToken":"..."}` 绑定 clientId（可选）。消息格式 `{"type","payload","ts"}`。事件类型全枚举：

| type | payload | 时机 |
|------|---------|------|
| tts | `{text,style,target,audioBase64,clientId}` | 每次话术播报（含唤醒应答/低电量/急停） |
| intent | `{clientId,channel,intent,ok,errorCode,utterance}` | 每个意图执行后 |
| site_changed | `{holderClientId,expiresAt,stolen}` 或 `{holderClientId:null,reason?}` | 解锁/结束/急停强制退出 |
| watchdog_stop | `{}` | 看门狗超时自动停车 |
| wake_armed | `{clientId,until}` | 唤醒武装/滚动续期 |
| asr_final | `{clientId,channel,text}` | ASR 出 final（TTS 打断信号） |
| flow_event | `{flowId,nodeId,nodeStatus,flowStatus}` | 任务流每次节点/流状态变化 |
| flow_jarvis_lost | `{flowId,failures}` | 断网轮询失败（每 2s） |
| flow_jarvis_back | `{flowId}` | 断网恢复（节点重发） |
| flow_charge_full | `{flowId,battery}` | 低电量充电至恢复阈值 |

### 2.3 /ws/high、/ws/low

透传代理 jarvis `ws://{jarvis}/ws/high|low`：仅把下行数据转发给客户端；任一侧关闭则两侧关闭。

## 3. FlowJSON Schema

```json
{
  "id": "（服务端生成或自带）",
  "name": "取货演示流程",
  "nodes": [{"id": "n1", "type": "focklift", "params": {"pos": 150}}],
  "edges": [{"from": "n1", "to": "n2", "on": "success"}],
  "parallel_groups": [["n3", "n4"]],
  "options": {"node_timeout_s": 120},
  "ui": {"positions": {"n1": {"x": 80, "y": 120}}, "viewport": {}}
}
```

- `type` ∈ focklift / head / follow_back / get_pallet / charge / drive。
- validate 规则（POST/PUT 时执行，400 返回 errors 数组）：节点类型合法；required 参数齐（复用 §4 schema，focklift 需 pos、drive 需 duration_s）；边 from/to 引用存在；`on` ∈ success|fail；每节点每类出边 ≤1 条；并行组引用存在且不重叠；入口节点（无入边，并行组视作整体）恰好 1 个；无环（缩点 DFS）。
- `options.node_timeout_s`（流级）、节点 `params.timeout_s`（节点级）可覆盖 config `taskflow.node_timeout_s`。
- `ui` 为前端布局扩展字段，后端不解析、原样存取。

## 4. 六任务参数表（GET /api/tasks/schemas 同源）

| 节点 | 参数 | 类型 | required | safety | default | 追问话术 |
|------|------|------|----------|--------|---------|----------|
| head | angle | number | ✓ | ✓ | — | 请告诉我旋转角度，多少度 |
| head | speed | number | | | 30 | |
| focklift | pos | number | ✓ | ✓ | — | 请告诉我目标叉高（毫米） |
| focklift | wait / tolerance | number | | | 20 / 20 | |
| follow_back | start_name | name | ✓ | ✓ | — | 请告诉我起点 |
| follow_back | target_name | name | ✓ | ✓ | — | 请告诉我终点 |
| follow_back | get_pallet | bool | | | false | |
| get_pallet | lift_height | number | | ✓ | 90 | （全默认不追问） |
| get_pallet | 其余 10 项 | number/bool | | | 见 schema | |
| charge | goal | name | | | "auto" | （不追问） |
| charge | side / angle / waitTime | number | | | 0 / 5 / 10 | |
| drive | duration_s | number | ✓ | | — | 请告诉我点动时长（秒） |
| drive | trans / rot / speed | number | | | 1 / 0 / 20 | |

确认策略：follow_back 参数齐后必须汇总确认（confirm=True）；head/get_pallet/charge 参数齐直接执行；货叉语音意图 |目标-当前| > confirm_threshold(100mm 可配) 时需确认。

## 5. 意图全表（app/nlu/rules.py）

| 意图 | 触发词示例 | 动作 |
|------|-----------|------|
| CONFIRM | 确认/是的/好/执行/对的 | 对话确认执行 |
| CANCEL | 取消/算了/不用了/停止确认 | 对话放弃 |
| FLOW_START | 执行/开始/运行 + 流程名 | 任务流匹配→确认→启动 |
| FLOW_PAUSE/RESUME/CANCEL | 暂停任务/继续任务/取消任务 | 流控 |
| STOP | 停止/停下/停车/急停/停 | watchdog.stop + 清唤醒武装 |
| MOVE_FWD/MOVE_BACK | 前进/往前/走吧；后退/往后/倒车 | 点动（看门狗） |
| TURN_LEFT/TURN_RIGHT | 左转/往左；右转/往右 | 点动转向 |
| TASK_HEAD | 原地转90度/左转45度/掉头 | head route（掉头=180°） |
| FORK_LIFT_UP/DOWN | 升起货叉/升一点；放下货叉/降一点 | 货叉至 max/min |
| FORK_LIFT_TO | 升到150毫米/升到1.5米/货叉调到210 | 货叉至指定高度（单位换算 mm） |
| SPEED_UP/DOWN/SET | 快点/加速；慢点/减速；速度调到35 / 35% | 调速（5~max） |
| IDLE / DOCK | 空闲/待机；回充/去充电 | control idle / dock |
| GOTO_GOAL | 去A区/前往B点 | control goto |
| TASK_FOLLOW_BACK | 从A点到B点/从A去B/盲叉取货 | follow_back route（盲叉→get_pallet=true） |
| TASK_GET_PALLET | 识别栈板/相机取货/自动取货 | get_pallet route |
| TASK_CHARGE | 去1号充电桩充电/去2号充电 | charge route |
| QUERY_BATTERY/MODE/POSE | 电量多少/什么模式/位置在哪 | get_state 查询 |
| QUERY_FORK_HEIGHT/MOTOR | 叉高多少/货叉多高；电机使能 | 同上 |
| QUERY_SPEED | 速度多少/当前速度/多快 | speed 百分比（缺则 vel m/s） |
| QUERY_TASK | 当前任务/在干什么/任务进度 | current_routes 状态 |
| QUERY_ALARM | 告警/报警 | 告警状态词 |
| QUERY_ALARM_EXPLAIN | 为什么停/怎么回事/怎么了/什么故障 | 告警解释答案库 |
| UNKNOWN | 其余 | fail_unknown 话术 |

所有意图在执行前按类别检查：运动级（MOTION/FORK/TASK）需现场锁 + cabin 需唤醒武装 + 任务流运行中拒绝；STOP/QUERY/CONFIRM/CANCEL/FLOW_* 放行。
