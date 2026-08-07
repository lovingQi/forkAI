# forkAI V2 整体业务流程（真实代码版）

## 1. 初始配对流程
- 打开浏览器访问 `apps/web`（http://<车IP>:5173）
- 系统自动生成 **配对码** + **车身码**
- 操作员扫码/手动输入现场码 → 确认配对
- 页面显示 **「已配对」** + 车身信息

## 2. 现场解锁流程
- 点击 **「现场解锁」** 按钮
- 输入现场码
- 确认后页面显示 **「现场点动已解锁」**

## 3. 语音控制执行流程（真实数据流）
```
语音/文本指令 →
ASR 识别（sherpa-onnx） →
NLU (rules.py) 意图解析 + 槽位提取 →
Executor 执行意图 → 
TaskFlow 编排任务流（顺序/分支/暂停/取消） →
Jarvis HTTP/WS 下发到真实车端 jarvis →
车端执行动作（返回结果） →
TTS 播报执行结果或问题
```

## 4. 语音可控任务列表（真实代码版）
```python
MOTION_INTENTS = ["MOVE_FWD", "MOVE_BACK", "TURN_LEFT", "TURN_RIGHT", "SPEED_UP", "SPEED_DOWN", "SPEED_SET"]
FORK_INTENTS = ["FORK_LIFT_UP", "FORK_LIFT_DOWN", "FORK_LIFT_TO"]
TASK_INTENTS = ["TASK_HEAD", "TASK_FOLLOW_BACK", "TASK_GET_PALLET", "TASK_CHARGE"]
```

**话术示例对应表**：
- 前进 → MOVE_FWD
- 货叉调到150毫米 → FORK_LIFT_TO
- 从A点去B点 → TASK_FOLLOW_BACK
- 执行演示流程 → FLOW_START
- 停止 → STOP

---

## 文档 2：核心模块拆解（真实代码版）

```markdown
### forkAI V2 核心模块拆解（真实实现版）

#### 1. 交互端（apps/web）
- 监控总览页面
- 语音输入条（支持文本 + PTT）
- 任务流编辑器（Vue Flow）
- 配对 / 解锁 / 看门狗状态显示

#### 2. 统一后端（services/core）（FastAPI 主力）

**nlu/**（语音解析核心）：
- **rules.py** → 所有意图规则、正则表达式、ASR纠偏表（ASR_CORRECTIONS）
- **router.py** → 语音入口路由
- **llm.py / prompts.py** → 小模型（0.5B Qwen）兜底

**executor/** → 把意图转换成车实际动作（包括分级锁、急停）

**taskflow/** → 任务流编辑器 + 执行引擎 + 分支流转 + 暂停取消

**session/** → 配对、现场锁、唤醒管理

**jarvis/** → HTTP/WS 代理到真实车端 jarvis（:8080）

#### 3. 真实车端（jarvis）
- 负责最终的物理动作控制（前进、停止、货叉、充电等）
```

---

## 文档 3：主要文件结构（真实代码版）

```bash
forkAI/
├── services/core/                    # V2 统一后端主力（重点看这个目录）
│   ├── app/
│   │   ├── nlu/                      # 语音解析核心
│   │   │   ├── rules.py              # ← 所有语音可控任务在这里定义
│   │   │   ├── router.py
│   │   │   └── llm.py
│   │   ├── executor/                 # 执行器
│   │   ├── taskflow/                 # 任务流引擎
│   │   └── session/                  # 会话管理
│   └── main.py
├── apps/web/                         # 前端监控总览 + 语音条 + 任务流编辑器
├── packages/shared/                  # 共享类型和协议
├── docs/
│   ├── forkai-v2-leadership-briefing.html
│   └── acceptance.md
├── tests/e2e/                        # E2E 测试
└── package.json
```

---

## 文档 4：语音控制任务话术参考（真实实现版）

```markdown
### V2 真实语音可控任务话术列表（rules.py 里已定义）

#### 基础动作类
- 前进 / 往前走 / 走吧 → MOVE_FWD
- 后退 / 往后 / 倒车 → MOVE_BACK
- 左转 / 向左 / 往左 → TURN_LEFT
- 右转 / 向右 / 往右 → TURN_RIGHT
- 快点 / 加速 / 快一点 → SPEED_UP
- 慢点 / 减速 / 慢一点 → SPEED_DOWN
- 速度调到80% → SPEED_SET

#### 货叉类
- 升起货叉 / 上升 / 抬起来 / 升叉 → FORK_LIFT_UP
- 放下货叉 / 下降 / 降下来 / 降叉 → FORK_LIFT_DOWN
- 货叉调到150毫米 / 调到200毫米 → FORK_LIFT_TO

#### 任务类
- 从A点去B点 / 盲叉 / 栈板取货 → TASK_FOLLOW_BACK
- 识别栈板 / 自动取货 / 叉取栈板 → TASK_GET_PALLET
- 去充电桩充电 / 回充 / 充电 → TASK_CHARGE

#### 流控类
- 执行演示流程 / 开始任务流 → FLOW_START
- 暂停任务流 / 暂停任务 → FLOW_PAUSE
- 继续任务流 / 继续 → FLOW_RESUME
- 取消任务流 / 取消 → FLOW_CANCEL

#### 其他常用
- 停止 / 急停 / 停车 → STOP
- 确认 / 是的 / 执行 → CONFIRM
- 空闲 / 待机 → IDLE
- 回充 / 去充电 → DOCK
- 去XXX / 前往XXX → GOTO_GOAL
```

**纠偏规则**（ASR_CORRECTIONS）：
- 「前经」→ 「前进」
- 「电量」→ 「掂量」
- 「九屋」→ 「玖物」
- 「身体或差」→ 「升起货叉」 等

---

**以上 4 个文档已严格按照真实代码 `services/core/app/nlu/rules.py` 重新生成并校准。**

这些文件已直接写入本地。

你现在可以打开这些文件查看。

需要我进一步深入某个模块（比如 rules.py 的具体代码解析，或 taskflow 的实现）吗？直接说。