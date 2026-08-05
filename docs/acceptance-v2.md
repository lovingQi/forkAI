# forkAI 验收清单 V2

- 版本：V2
- 日期：2026-08-04
- 对应代码：services/core Week 1-4 完成（mock 全自动回归通过）

## 1. 验收环境

mock 全自动环境（三服务）：

```bash
node services/mock-jarvis/src/index.js                 # :8080
services/llm-sidecar/run-llama-server.sh               # :19002（复合指令/回归第4项需要）
cd services/core && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 19000
```

自动回归脚本（services/core 目录下）：

```bash
.venv/bin/python scripts/week2_regression.py     # 闸门A+六任务+语音体验+降级，9 项
.venv/bin/python scripts/week3_engine_test.py    # 任务流引擎 7 项（内嵌 week2 全量）
.venv/bin/python scripts/week4_voice_flow_test.py # 语音触发任务流+异常收尾 9 项
```

最近一次全量结果：week2 9/9 PASS、week3 7/7 PASS、week4 9/9 PASS（2026-08-04/05，x86 开发机）。

## 2. 闸门 A 回归（V1 清单 → V2 脚本断言）

| V1 闸门A 项 | V2 对应断言 |
|-------------|-------------|
| 1 配对 | week2 #1 配对+解锁 |
| 2 现场解锁 | week2 #1（409 自动 force 接管） |
| 3 前进话术 | week2 #2 点动 MOVE_FWD；#6 提示音前缀 |
| 4 看门狗 2s 自动停 | mock 日志 action=stop（week2 #2 后自动发生） |
| 5 调速话术 | week2 前置链路覆盖（speed_set 意图单测 rules） |
| 6 停止 | week2 #7 后"停止"；week3 #6 锁定中停止放行 |
| 7 未解锁拒绝+停止可用 | week4 #7 急停退出后 fail_no_site；week2 全流程 |

## 3. 六任务验收表

| 任务 | 指令示例 | 预期话术 | mock 断言（脚本项） |
|------|----------|----------|---------------------|
| drive 点动 | 前进 | 好的，前进 | week2 #2（drive payload + 2s 看门狗 stop） |
| head 旋转 | 原地转90度 | 好的，原地转90度 | week2 #2（pose[2] 变化 +1.57rad） |
| focklift 货叉 | 升到150毫米 | 好的，货叉调到150毫米 | week2 #2（fork_height==150）；大幅确认流 week2 回归含货叉确认 |
| follow_back 点到点 | 从A点到B点 → 确认 | 确认执行：…吗 → 好的，从…到… | week2 #2（确认流）+ #3（追问流） |
| get_pallet 取货 | 识别栈板 | 好的，开始栈板识别取货 | week2 #2（status=栈板识别取货完成） |
| charge 充电 | 去1号充电桩充电 | 好的，前往1号充电桩充电 | week2 #2（charing=true，stop 复位） |

## 4. 任务流验收（week3/week4 脚本项）

| 场景 | 脚本断言 |
|------|----------|
| 建流/列表/中文名 | week3 #1 建流；week4 #1 两条中文流程 |
| 顺序执行三节点 succeeded | week3 #1（轨迹打印） |
| 节点超时 + fail 分支 | week3 #2（n1 failed → charge succeeded） |
| 暂停/继续（重发当前节点） | week3 #3；week4 #3（语音） |
| 取消（剩余 skipped） | week3 #4 |
| 低电量自动暂停+充电+充满广播 | week3 #5 |
| 断网续跑（lost/back 广播） | week4 #6 |
| 急停强制退出现场 | week4 #7 |
| 快照恢复（重启→paused） | week4 #8 |
| 分级锁定 | week3 #6 / week4 #5 |
| 语音触发（精确/模糊/不存在） | week4 #2/#4 |
| 配对码一次性/现场码轮换 | week4 #9 |

## 5. 性能指标

| 指标 | 目标 | 实测（x86 开发机） |
|------|------|---------------------|
| ASR | RTF<1（延迟<1s） | RTF≈0.06（num_threads=1，14M int8）；RK3588 待复核 |
| LLM 意图抽取 | <3s | 0.5B Q4_K_M 本地 <1s；超时上限 timeout_s=3 可配 |
| TTS | — | piper RTF≈0.1 |
| 任务成功率 | >95% | 试点统计方法：文本日志（保留 7 天）中 intents ok=true 占比，按周统计；mock 环境回归 100% 不构成试点数据 |

## 6. 真车待办清单（人工项）

1. **步骤21（真车 route 语义）**：`POST /api/control/schedulerthis` 的 HTTP 映射与五种节点（head/focklift/follow_back/get_pallet/charge）参数语义验证；校准 `FlowEngine._is_node_done` 完成判定（当前为 mock 语义 current_routes.status==finished）。
2. **步骤50（ASR 真机）**：RK3588 上 sherpa-onnx aarch64 推理 RTF/线程数复核；麦克风采集（cabin_listen.enabled）实测。
3. **步骤51（LLM 真机）**：llama.cpp aarch64 编译（-DGGML_NATIVE=OFF）与 Qwen2-0.5B/1.5B 选型复核。
4. **步骤58（试点部署）**：整车联调、站点名大小写口径确认（当前 norm 小写化）、音区实测唤醒率。
5. 提示音音量/音色真机听感确认（assets/beep_*.wav 可替换）。

—— 真车联调完成后填 docs/test-report.md（本版不含）。
