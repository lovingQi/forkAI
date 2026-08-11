---
name: run-forklift-ai-studio
description: 组织多角色开发团队完成叉车、AGV、机器人语音控制和车载 AI 系统的需求澄清、架构设计、跨仓开发、功能安全审查、测试验证、真车联调与发布收口。用于涉及 forkAI、jarvis-fork、C++/ROS、FastAPI、Vue、ASR/NLU/LLM/TTS、任务流、车辆 HTTP/WebSocket 协议、RK3588 部署，或用户要求由不同 Agent 分工、评审、会签和交付的任务。
---

# 运行叉车 AI 研发工作室

## 基本原则

把用户视为需求、安全、架构和真车操作的最终批准人。让工作室总负责人统筹七个部门；让部门成员执行，让部门负责人评审和收口。始终遵守更高优先级指令、当前协作模式和仓库内 `AGENTS.md`，不得以本 Skill 绕过审批。

不得把 Mock、仿真或开发机结果描述为真车或目标硬件验收。不得让负责人单独批准自己直接实现的成果。不得在没有明确授权和已确认安全环境时启动、控制或部署真实车辆。

## 加载参考

开始任务时按下列规则读取参考文件：

- 始终读取 [studio-architecture.md](references/studio-architecture.md)、[operating-workflow.md](references/operating-workflow.md) 和 [task-routing.md](references/task-routing.md)。
- 在制定计划、实施、测试或审查前读取 [quality-gates.md](references/quality-gates.md)。
- 任务涉及 `/home/xbl/Desktop/learn/forkAI` 时读取 [forkai-project.md](references/forkai-project.md)。
- 任务涉及 `/home/xbl/Desktop/learn/forkAI` 时，按 `.agents/skills/forkai-ledger/SKILL.md` 在任务简报时读取 `docs/project-ledger.md`，并在总收口时按其规范更新台账。
- 任务涉及 `/home/xbl/Desktop/jarvis-fork`、车端协议或物理动作时读取 [jarvis-fork-project.md](references/jarvis-fork-project.md)。

## 分级任务

先进行只读勘察，再由总负责人确定等级和参与部门：

| 等级 | 定义 | 处理权限 |
|---|---|---|
| L0 | 解释、盘点、只读诊断 | 可自动分派，禁止写入 |
| L1 | 单模块、行为边界明确且非安全关键 | 在用户已批准范围内执行 |
| L2 | 跨模块、跨服务或跨仓接口变化 | 先完成架构与受影响部门会签 |
| L3 | 需求语义、安全策略、物理动作、重大架构、真车或部署 | 停在批准点，取得用户明确许可 |

等级不确定时按较高等级处理。发现实际范围超出已批准任务时，停止实施并重新分级。

## 组织团队

由总负责人选择主责部门、协作部门和收口负责人。优先启用真实独立 Agent；环境不支持时按同一职责顺序执行，并明确说明未形成独立 Agent 评审。

使用多 Agent 时：

1. 把边界清晰、互不写冲突的勘察、设计或验证工作并行分派。
2. 向执行成员提供任务事实、允许范围和交付格式，不泄漏预期结论给独立评审者。
3. 由部门负责人审查原始产物、差异和测试证据，给出 `通过`、`有条件通过` 或 `驳回`。
4. 负责人参与实现时，指定另一受影响部门负责人交叉复核。
5. 让总负责人汇总各部门结论；不得在必需签署缺失或 P0/P1 风险未关闭时宣布完成。

## 执行工作流

按 [operating-workflow.md](references/operating-workflow.md) 依次执行：

1. 建立任务简报，固定目标、非目标、约束、仓库和验收条件。
2. 读取代码、文档、历史与工作区状态，禁止覆盖用户改动。
3. 分级风险并依据 [task-routing.md](references/task-routing.md) 组建团队。
4. 形成方案和验证矩阵，取得所需负责人和用户批准。
5. 在批准边界内实施，让各执行 Agent 只拥有清晰文件范围。
6. 执行 [quality-gates.md](references/quality-gates.md) 中与变更匹配的验证。
7. 完成部门评审、跨部门会签、遗留风险登记和总负责人收口。

## 升级审批

遇到以下任一情况时暂停并请求用户决定：

- 产品目标、用户可见语义、验收条件或任务范围发生变化。
- 急停、看门狗、现场锁、速度限制、故障降级或其他安全策略变化。
- 新增服务、替换核心技术、改变跨仓边界或形成不可逆迁移。
- 需要启动真实车辆、下发物理动作、写入真车配置或在目标工控机部署。
- 需要触碰冻结栈、第三方大目录、生产数据，或执行提交、推送、删除等外部状态变更。

## 交付格式

每次交付至少报告：任务等级、参与部门、各负责人结论、修改文件、验证证据、未执行的验证、遗留风险和用户后续批准项。区分 `Mock 已验证`、`HIL 已验证`、`真车已验证` 与 `目标硬件已验证`，不得混用。
