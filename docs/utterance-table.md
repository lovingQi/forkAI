# 话术表（zh-CN）

人设：冷静女声操作员；成功短句；失败略慢并带原因。

| 键 | 文本 | 场景 |
|----|------|------|
| wake_ack | 在 | 车载唤醒成功 |
| move_fwd | 好的，前进 | 运动 |
| move_back | 好的，后退 | 运动 |
| turn_left | 好的，左转 | 运动 |
| turn_right | 好的，右转 | 运动 |
| stop | 已停止 | 运动 |
| speed_up | 好的，加速 | 调速 |
| speed_down | 好的，减速 | 调速 |
| speed_set | 已调到百分之{n} | 调速 |
| idle | 好的，空闲 | 任务 |
| dock | 好的，回充 | 任务 |
| goto | 好的，前往{goal} | 任务 |
| query_battery | 电量百分之{n} | 问答 |
| query_mode | 当前模式{mode} | 问答 |
| query_pose | 位置 X{x} Y{y} | 问答 |
| query_fork | 叉高{n}毫米 | 问答 |
| query_motor | 电机{state} | 问答 |
| query_alarm | 告警状态{alarm} | 问答 |
| query_status | 电量百分之{n}，模式{mode}，{task}，告警{alarm} | 问答 |
| fail_unpaired | 失败：未配对 | 错误 |
| fail_no_site | 失败：未现场解锁 | 错误 |
| fail_lock | 失败：无点动控制权 | 错误 |
| fail_unknown | 失败：未识别指令 | 错误 |
| fail_asr | 失败：识别失败 | 错误 |
| fail_nlu | 没听清，请再说一次 | NLU/LLM 超时或非 JSON，整句不执行 |
| fail_goto | 失败：站点无效 | 错误 |
| fail_generic | 失败：{reason} | 错误 |
