"""jarvis 路线构建（步骤29：全任务）。

route 节点格式（JMode* 执行，jarvis 侧已研究确认）：
- head（原地旋转 JModeHead）：
  {"cmd":"head","angle":度(可负),"speed":度/秒,"steer_angle":0,"use_pid":true}
- focklift（货叉 JModeFocklift）：
  {"cmd":"focklift","pos":mm,"wait":秒,"tolerance":mm}
- follow_back（点到点/盲叉 JModeFollowBack）：
  {"cmd":"follow_back","start_name":...,"target_name":...,"get_pallet":bool,
   "is_target":false,"rms_task":false,"init_task_id":""}
- get_pallet（相机栈板识别 JModeAutoGetPallet）：
  {"cmd":"get_pallet","get_pallet":true,"pallet_length":0.0,"stop_dis":0.0,
   "lift_height":90,"observe_height":75,"get_pallet_height":75,"need_extend":false,
   "need_check":false,"pallet_x":0.0,"pallet_y":0.0,"need_scan_barcode":false}
- charge（自动充电 JModeFltCharge）：
  {"cmd":"charge","goal":"充电桩名或auto","side":0,"angle":5,"waitTime":10}

build_route(task_type, params) 通用入口；缺 required 参数抛 ValueError；
未知 task_type 抛 NotImplementedError。
"""
from ..config import now_ms


def build_fork_route(pos: int, wait: int, tolerance: int) -> dict:
    """单节点货叉升降路线。"""
    return {
        "name": f"voice_focklift_{now_ms()}",
        "content": {"a": {"cmd": "focklift", "pos": int(pos), "wait": int(wait), "tolerance": int(tolerance)}},
    }


def _require(params: dict, *names: str) -> None:
    missing = [n for n in names if params.get(n) is None]
    if missing:
        raise ValueError(f"缺 required 参数: {', '.join(missing)}")


def _build_head(params: dict) -> dict:
    _require(params, "angle")
    return {
        "cmd": "head",
        "angle": params["angle"],
        "speed": params.get("speed", 30),
        "steer_angle": 0,
        "use_pid": True,
    }


def _build_focklift(params: dict) -> dict:
    _require(params, "pos")
    return {
        "cmd": "focklift",
        "pos": int(params["pos"]),
        "wait": int(params.get("wait", 20)),
        "tolerance": int(params.get("tolerance", 20)),
    }


def _build_follow_back(params: dict) -> dict:
    _require(params, "start_name", "target_name")
    return {
        "cmd": "follow_back",
        "start_name": params["start_name"],
        "target_name": params["target_name"],
        "get_pallet": bool(params.get("get_pallet", False)),
        "is_target": False,
        "rms_task": False,
        "init_task_id": "",
    }


def _build_get_pallet(params: dict) -> dict:
    # 全部有默认（schema 保证），缺 required 的情况不存在
    return {
        "cmd": "get_pallet",
        "get_pallet": bool(params.get("get_pallet", True)),
        "pallet_length": float(params.get("pallet_length", 0.0)),
        "stop_dis": float(params.get("stop_dis", 0.0)),
        "lift_height": params.get("lift_height", 90),
        "observe_height": params.get("observe_height", 75),
        "get_pallet_height": params.get("get_pallet_height", 75),
        "need_extend": bool(params.get("need_extend", False)),
        "need_check": bool(params.get("need_check", False)),
        "pallet_x": float(params.get("pallet_x", 0.0)),
        "pallet_y": float(params.get("pallet_y", 0.0)),
        "need_scan_barcode": bool(params.get("need_scan_barcode", False)),
    }


def _build_charge(params: dict) -> dict:
    return {
        "cmd": "charge",
        "goal": params.get("goal") or "auto",
        "side": int(params.get("side", 0)),
        "angle": int(params.get("angle", 5)),
        "waitTime": int(params.get("waitTime", 10)),
    }


_BUILDERS = {
    "head": _build_head,
    "focklift": _build_focklift,
    "follow_back": _build_follow_back,
    "get_pallet": _build_get_pallet,
    "charge": _build_charge,
}


def build_route(task_type: str, params: dict) -> dict:
    """{"name": f"voice_{cmd}_{ts}", "content": {"a": {节点}}}。

    节点表必须在 "content" 键下（JRoutes::Start(JArg) 只认 routes/key/id/content，
    JRoutes.h:45 + GetRKICFromArg 反汇编键名）。缺 required 抛 ValueError。
    """
    builder = _BUILDERS.get(task_type)
    if builder is None:
        raise NotImplementedError(f"route task_type 未实现: {task_type}")
    node = builder(params or {})
    # mock_fail 透传（mock-jarvis 联调用：模拟任务失败路径；真车 jarvis 会忽略未知键）
    if (params or {}).get("mock_fail"):
        node["mock_fail"] = True
    return {"name": f"voice_{task_type}_{now_ms()}", "content": {"a": node}}
