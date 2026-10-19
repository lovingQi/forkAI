"""追踪文案：意图 / 任务流节点 → 调用式 + 中文标签。只做字符串格式化。"""

from .nlu.rules import FORK_INTENTS, TASK_INTENTS

# 会向车端下发动作的意图。成功后步骤记 sent（已下发），不记 done。
SENT_INTENTS = frozenset(
    {
        "MOVE_FWD",
        "MOVE_BACK",
        "TURN_LEFT",
        "TURN_RIGHT",
        "DRIVE_COMBINED",
        "STOP",
        "IDLE",
        "DOCK",
        "GOTO_GOAL",
        *FORK_INTENTS,
        *TASK_INTENTS,
    }
)

INTENT_CALLS = {
    "MOVE_FWD": ("drive.forward()", "前进"),
    "MOVE_BACK": ("drive.back()", "后退"),
    "TURN_LEFT": ("drive.turn_left()", "左转"),
    "TURN_RIGHT": ("drive.turn_right()", "右转"),
    "DRIVE_COMBINED": ("drive.combined({move},{turn})", "复合点动 {move}+{turn}"),
    "SPEED_UP": ("drive.speed_up()", "加速"),
    "SPEED_DOWN": ("drive.speed_down()", "减速"),
    "SPEED_SET": ("drive.speed_set({n})", "速度设为 {n}mm/s"),
    "STOP": ("drive.stop()", "停止"),
    "IDLE": ("nav.idle()", "空闲待机"),
    "DOCK": ("nav.dock()", "回充"),
    "GOTO_GOAL": ('nav.goto("{goal}")', "前往 {goal}"),
    "FORK_LIFT_UP": ("fork.lift_up()", "升起货叉"),
    "FORK_LIFT_DOWN": ("fork.lift_down()", "放下货叉"),
    "FORK_LIFT_TO": ("fork.lift(pos={n})", "货叉调到 {n}mm"),
    "QUERY_BATTERY": ("query.battery()", "查询电量"),
    "QUERY_MODE": ("query.mode()", "查询模式"),
    "QUERY_POSE": ("query.pose()", "查询位置"),
    "QUERY_FORK_HEIGHT": ("query.fork_height()", "查询叉高"),
    "QUERY_MOTOR": ("query.motor()", "查询电机"),
    "QUERY_ALARM": ("query.alarm()", "查询告警"),
    "QUERY_SPEED": ("query.speed()", "查询速度"),
    "QUERY_TASK": ("query.task()", "查询当前任务"),
    "QUERY_ALARM_EXPLAIN": ("query.alarm_explain()", "解释告警"),
    "QUERY_STATUS": ("query.status()", "查询车况"),
    "TASK_HEAD": ("task.head(angle={angle})", "原地旋转 {angle}°"),
    "TASK_FOLLOW_BACK": (
        "task.follow_back({start_name}→{target_name})",
        "点到点 {start_name}→{target_name}",
    ),
    "TASK_GET_PALLET": ("task.get_pallet()", "栈板识别取货"),
    "TASK_CHARGE": ("task.charge(goal={goal})", "去充电"),
    "CONFIRM": ("dialog.confirm()", "确认"),
    "CANCEL": ("dialog.cancel()", "取消"),
    "FLOW_START": ('flow.start("{name}")', "执行任务流 {name}"),
    "FLOW_PAUSE": ("flow.pause()", "暂停任务流"),
    "FLOW_RESUME": ("flow.resume()", "继续任务流"),
    "FLOW_CANCEL": ("flow.cancel()", "取消任务流"),
    "UNKNOWN": ("unknown()", "未识别"),
    "WAKE": ("wake.ack()", "唤醒应答"),
    "LLM_FAIL": ("nlu.fail()", "理解失败"),
}

NODE_CALLS = {
    "focklift": ("fork.lift(pos={pos})", "货叉升降 {pos}mm"),
    "head": ("task.head(angle={angle})", "原地旋转 {angle}°"),
    "follow_back": (
        "task.follow_back({start_name}→{target_name})",
        "点到点 {start_name}→{target_name}",
    ),
    "get_pallet": ("task.get_pallet()", "栈板识别取货"),
    "charge": ("task.charge(goal={goal})", "自动充电 {goal}"),
    "drive": ("drive.timed({duration_s}s)", "定时点动 {duration_s}s"),
}


class _Safe(dict):
    def __missing__(self, key):
        return "?"


def _fill(template: str, slots: dict) -> str:
    safe = {}
    for k, v in (slots or {}).items():
        safe[k] = "?" if v is None or v == "" else v
    try:
        return template.format_map(_Safe(safe))
    except Exception:
        return template


def _pair(table: dict, key: str, slots: dict) -> tuple[str, str]:
    call_t, label_t = table.get(key, (f"{key}()", key))
    return _fill(call_t, slots), _fill(label_t, slots)


def intent_step(intent: dict, idx: int) -> dict:
    name = (intent or {}).get("name") or "?"
    slots = (intent or {}).get("slots") if isinstance((intent or {}).get("slots"), dict) else {}
    call, label = _pair(INTENT_CALLS, name, slots)
    return {
        "id": f"s{idx}",
        "call": call,
        "label": label,
        "status": "queued",
        "inserted": False,
        "note": None,
    }


def node_step(node: dict) -> dict:
    ntype = (node or {}).get("type") or "?"
    params = (node or {}).get("params") if isinstance((node or {}).get("params"), dict) else {}
    call, label = _pair(NODE_CALLS, ntype, params)
    return {
        "id": (node or {}).get("id") or "?",
        "call": call,
        "label": label,
        "status": "queued",
        "inserted": False,
        "note": None,
    }


def _slot_text(slots: dict) -> str:
    parts = []
    for k, v in (slots or {}).items():
        if v is None or v == "":
            continue
        parts.append(f"{k}={v}")
    return f"({', '.join(parts)})" if parts else ""


def fmt_intents(intents: list) -> str:
    bits = []
    for item in intents or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("intent") or "?"
        slots = item.get("slots") if isinstance(item.get("slots"), dict) else {}
        bits.append(f"{name}{_slot_text(slots)}")
    return " → ".join(bits)
