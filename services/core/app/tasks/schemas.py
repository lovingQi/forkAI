"""TASK_* 任务参数 schema（步骤27）。

每个任务：
- route_cmd: jarvis route 节点 cmd（见 routes_builder）
- params: {参数名: {type, required, safety, default, ask}}
  - required+safety 且用户没说 → ParamDialogue 依次追问（ask 话术）
  - safety 但有默认（如 get_pallet.lift_height=90）→ 不追问，汇总确认时复述
  - 无 default 且 required → 缺参时 routes_builder 抛 ValueError（兜底）
- confirm: True → 参数齐后仍需汇总确认（confirm_task 话术）才执行；
  False → 参数齐直接执行（head/get_pallet/charge 现状，注释见 executor）
- utterance: 执行成功话术 key（utterances.zh-CN.json）

意图槽位名与 schema 参数名一致（rules.py / prompts.py 同步）。
drive 点动不入 schema（走 watchdog 现状不动）。
"""

TASK_SCHEMAS = {
    "TASK_HEAD": {
        "route_cmd": "head",
        "confirm": False,
        "params": {
            "angle": {
                "type": "number",
                "required": True,
                "safety": True,
                "ask": "请告诉我旋转角度，多少度",
            },
            "speed": {"type": "number", "required": False, "default": 30},
        },
        "utterance": "task_head",
    },
    "TASK_FOLLOW_BACK": {
        "route_cmd": "follow_back",
        "confirm": True,  # 起止点均为 safety：集齐后必须汇总确认
        "params": {
            "start_name": {
                "type": "name",
                "required": True,
                "safety": True,
                "ask": "请告诉我起点",
            },
            "target_name": {
                "type": "name",
                "required": True,
                "safety": True,
                "ask": "请告诉我终点",
            },
            "get_pallet": {"type": "bool", "required": False, "default": False},
        },
        "utterance": "task_follow_back",
    },
    "TASK_GET_PALLET": {
        "route_cmd": "get_pallet",
        "confirm": False,  # 全部参数有默认（lift_height=90 标 safety 但有默认）→ 直接执行
        "params": {
            "get_pallet": {"type": "bool", "required": False, "default": True},
            "pallet_length": {"type": "number", "required": False, "default": 0.0},
            "stop_dis": {"type": "number", "required": False, "default": 0.0},
            "lift_height": {"type": "number", "required": False, "safety": True, "default": 90},
            "observe_height": {"type": "number", "required": False, "default": 75},
            "get_pallet_height": {"type": "number", "required": False, "default": 75},
            "need_extend": {"type": "bool", "required": False, "default": False},
            "need_check": {"type": "bool", "required": False, "default": False},
            "pallet_x": {"type": "number", "required": False, "default": 0.0},
            "pallet_y": {"type": "number", "required": False, "default": 0.0},
            "need_scan_barcode": {"type": "bool", "required": False, "default": False},
        },
        "utterance": "task_get_pallet",
    },
    "TASK_CHARGE": {
        "route_cmd": "charge",
        "confirm": False,  # goal 默认 auto（不追问），直接执行
        "params": {
            "goal": {"type": "name", "required": False, "default": "auto"},
            "side": {"type": "number", "required": False, "default": 0},
            "angle": {"type": "number", "required": False, "default": 5},
            "waitTime": {"type": "number", "required": False, "default": 10},
        },
        "utterance": "task_charge",
    },
}


def summary_for(intent: str, params: dict) -> str:
    """汇总确认文本（confirm_task 的 {summary}）。"""
    if intent == "TASK_HEAD":
        return f"原地转{params.get('angle')}度"
    if intent == "TASK_FOLLOW_BACK":
        s = f"从{params.get('start_name')}到{params.get('target_name')}"
        if params.get("get_pallet"):
            s += "并取货"
        return s
    if intent == "TASK_GET_PALLET":
        return f"栈板识别取货（叉高{params.get('lift_height', 90)}毫米）"
    if intent == "TASK_CHARGE":
        return f"前往{params.get('goal')}充电"
    return intent


def utterance_params_for(intent: str, params: dict) -> dict:
    """执行话术模板参数。"""
    if intent == "TASK_HEAD":
        return {"n": params.get("angle")}
    if intent == "TASK_FOLLOW_BACK":
        return {"start": params.get("start_name"), "goal": params.get("target_name")}
    if intent == "TASK_CHARGE":
        return {"goal": params.get("goal")}
    return {}
