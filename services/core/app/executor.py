"""IntentExecutor：意图执行 1:1 迁移 executor.ts + 货叉升降（步骤18-20）+ TASK_* 任务（步骤27-30）。

返回 dict：{"ok", "error_code", "utterance_key", "utterance_params", "speak_kind", "speak_style"}
特殊键：speak_text（动态话术，不进 utterances 模板）、awaiting（多轮对话挂起中，
run_utterance 遇到应中断复合指令链）。
- STOP 直达并清唤醒武装
- 运动/货叉/TASK_* 意图查现场锁；cabin 通道另查唤醒武装
- MOTION 走 watchdog；IDLE/DOCK/GOTO 走 jarvis control；QUERY 走 get_state
- 货叉/TASK_* 走 jarvis 内联路线（/api/control/scheduler）
- ParamDialogue 统一管：TASK_* 缺参追问 → 集齐汇总确认；货叉超阈值安全确认
"""
import math

from .jarvis.routes_builder import build_fork_route, build_route
from .jarvis.station_names import resolve_station_fields, resolve_station_name
from .nlu.rules import FORK_INTENTS, MOTION_INTENTS, TASK_INTENTS
from .taskflow.engine import FlowBusyError
from .taskflow.matcher import match_flow_name
from .tasks.dialogue import ParamDialogue
from .tasks.schemas import TASK_SCHEMAS, summary_for, utterance_params_for

_ALARM_MAP = {"normal": "正常", "estop": "急停", "lost": "定位丢失", "stuck": "受困"}

# QUERY_ALARM_EXPLAIN 答案库（步骤32）：给原因与处置建议；未知 alarm 原样播报
_ALARM_EXPLAIN = {
    "normal": "当前没有告警，车辆正常",
    "estop": "检测到急停触发，请检查急停按钮和安全区域",
    "lost": "定位丢失，建议重新定位或检查反光板与地图",
    "stuck": "车辆受困，前方路径被阻挡，请清除障碍",
}

# QUERY_MODE：真车 JMode 名 → 中文话术（依据各 JMode 构造函数名）
_MODE_MAP = {
    "Idle": "空闲",
    "ModeFocklift": "货叉任务",
    "ModeHead": "原地旋转",
    "ModeFollowBack": "点到点任务",
    "ModeAutoGetPallet": "栈板取货",
    "ModeCharge": "充电",
    "ModeGoto": "前往目标",
    "ModeDrive": "行驶",
}


def _ok(key: str, kind: str) -> dict:
    return {"ok": True, "utterance_key": key, "speak_kind": kind, "speak_style": "ok"}


def _speak(text: str) -> dict:
    """动态话术结果（追问等，不进 utterances 模板）；awaiting 标记挂起中。"""
    return {"ok": True, "speak_text": text, "speak_kind": "move", "speak_style": "ok", "awaiting": True}


def _fail(code: str, key: str) -> dict:
    return {
        "ok": False,
        "error_code": code,
        "utterance_key": key,
        "speak_kind": "fail",
        "speak_style": "fail",
    }


def _fail_reason(reason: str) -> dict:
    return {
        "ok": False,
        "utterance_key": "fail_generic",
        "utterance_params": {"reason": reason},
        "speak_kind": "fail",
        "speak_style": "fail",
    }


def _js_round(v: float) -> int:
    # JS Math.round：向 +inf 半进一
    return int(math.floor(v + 0.5))


class IntentExecutor:
    def __init__(self, cfg: dict, sessions, jarvis, watchdog):
        self._cfg = cfg
        self._sessions = sessions
        self._jarvis = jarvis
        self._watchdog = watchdog
        # 多轮对话挂起态：TASK_* 参数追问/汇总确认 + 货叉安全确认（原 PendingStore 迁入）
        self._dialogue = ParamDialogue()
        # 任务流引擎（main 注入）：running|paused 时运动级指令分级锁定
        self._flow_engine = None
        # 任务流存储（main 注入）：FLOW_START 按名查找
        self._flow_store = None

    def set_flow_engine(self, engine) -> None:
        self._flow_engine = engine

    def set_flow_store(self, store) -> None:
        self._flow_store = store

    async def handle(self, intent: dict, ctx: dict) -> dict:
        name = intent["name"]
        client_id = ctx["client_id"]

        # 30s 无应答惰性超时：晚到的 CONFIRM/CANCEL 回 timeout_cancel
        if self._dialogue.check_timeout(client_id) and name in ("CONFIRM", "CANCEL"):
            return _fail("timeout", "timeout_cancel")

        # 有挂起对话：优先喂给对话（计划步骤30"后续输入优先喂给 dialogue 续答"）；
        # 注意必须在 UNKNOWN 早退之前——collect 阶段的参数回答（如"A点"）大多
        # 解析为 UNKNOWN，应作为参数值消费而不是报错
        session = self._dialogue.current(client_id)
        if session is not None:
            if name == "CANCEL":
                self._dialogue.cancel(client_id)
                return _ok("cancel_ok", "move")
            if name == "STOP":
                # 安全优先：放弃对话并继续走 STOP 流程
                self._dialogue.cancel(client_id)
            elif session["stage"] == "confirm":
                if name == "CONFIRM":
                    done = self._dialogue.pop(client_id)
                    return await self._dispatch_exec(done["exec"], ctx)
                # confirm 阶段收到新指令：放弃旧确认，按新指令处理（注释说明：
                # 避免"说好却执行了上一条任务"的歧义）
                self._dialogue.cancel(client_id)
            else:  # collect：当前输入视为参数答案
                if name == "CONFIRM":
                    # 参数没收齐时说"确认"→ 重问当前参数
                    spec = session["schema"]["params"][session["missing"][0]]
                    return _speak(spec["ask"])
                feed = self._dialogue.feed_collect(client_id, ctx.get("text") or intent.get("raw_text") or "")
                if feed["action"] in ("ask", "reask"):
                    return _speak(feed["prompt"])
                return {
                    "ok": True,
                    "utterance_key": "confirm_task",
                    "utterance_params": {"summary": feed["summary"]},
                    "speak_kind": "move",
                    "speak_style": "ok",
                    "awaiting": True,
                }

        if name == "UNKNOWN":
            return _fail("unknown", "fail_unknown")
        if name == "CONFIRM":
            return _fail("unknown", "fail_unknown")
        if name == "CANCEL":
            return _fail("unknown", "fail_unknown")

        if name == "STOP":
            try:
                await self._watchdog.stop(False)
            except Exception:
                # still acknowledge stop
                pass
            self._sessions.clear_wake_arm()
            return _ok("stop", "move")

        # FLOW_* 流控意图：不参与分级锁定（它们就是流控本身），也不要求现场锁
        # （与 /api/flows/{id}/start 的 API 语义一致——任务流与点动分级，见 routes_flows.py 注释）
        if name == "FLOW_START":
            return await self._handle_flow_start(intent, ctx)
        if name == "FLOW_PAUSE":
            return await self._handle_flow_ctl("pause")
        if name == "FLOW_RESUME":
            return await self._handle_flow_ctl("resume")
        if name == "FLOW_CANCEL":
            return await self._handle_flow_ctl("cancel")

        needs_site = name in MOTION_INTENTS or name in FORK_INTENTS or name in TASK_INTENTS
        if needs_site:
            # 分级锁定（步骤39）：任务流执行/暂停中，运动级指令拒绝；
            # STOP/QUERY_*/CONFIRM/CANCEL/对话续答 不受影响
            engine = self._flow_engine
            if engine is not None and engine.is_busy():
                return _fail("flow_running", "fail_flow_running")
            if not self._sessions.has_site(ctx["client_id"]):
                return _fail("no_site", "fail_no_site")
            if (
                ctx["channel"] == "cabin"
                and not ctx.get("wake_ok")
                and not self._sessions.is_wake_armed(ctx["client_id"])
            ):
                return {
                    "ok": False,
                    "error_code": "not_armed",
                    "utterance_key": "fail_generic",
                    "utterance_params": {"reason": "请先说玖物玖物"},
                    "speak_kind": "fail",
                    "speak_style": "fail",
                }

        if name == "MOVE_FWD":
            return await self._drive(1, 0, "move_fwd")
        if name == "MOVE_BACK":
            return await self._drive(-1, 0, "move_back")
        if name == "TURN_LEFT":
            return await self._drive(0, 1, "turn_left")
        if name == "TURN_RIGHT":
            return await self._drive(0, -1, "turn_right")

        if name in FORK_INTENTS:
            return await self._handle_fork(intent, ctx)

        if name in TASK_INTENTS:
            return await self._handle_task(intent, ctx)

        if name == "SPEED_UP":
            self._watchdog.bump_speed(self._cfg["speed"]["step"])
            return _ok("speed_up", "move")
        if name == "SPEED_DOWN":
            self._watchdog.bump_speed(-self._cfg["speed"]["step"])
            return _ok("speed_down", "move")
        if name == "SPEED_SET":
            self._watchdog.set_speed(intent["slots"].get("n") or self._watchdog.speed)
            return {
                "ok": True,
                "utterance_key": "speed_set",
                "utterance_params": {"n": self._watchdog.speed},
                "speak_kind": "move",
                "speak_style": "ok",
            }

        if name == "IDLE":
            await self._jarvis.control("idle")
            return _ok("idle", "move")
        if name == "DOCK":
            await self._jarvis.control("dock")
            return _ok("dock", "move")
        if name == "GOTO_GOAL":
            goal = str(intent["slots"].get("goal") or "").strip()
            if not goal:
                return _fail("goto", "fail_goto")
            goal = resolve_station_name(goal, await self._jarvis.path_point_names())
            res = await self._jarvis.control("goto", {"target": "goal", "goal": goal})
            if res and res.get("succeed") is False:
                return _fail("goto", "fail_goto")
            return {
                "ok": True,
                "utterance_key": "goto",
                "utterance_params": {"goal": goal},
                "speak_kind": "move",
                "speak_style": "ok",
            }

        return await self._handle_query(name)

    async def _drive(self, trans: int, rot: int, key: str) -> dict:
        try:
            await self._watchdog.drive(trans, rot)
        except Exception as e:
            return _fail_reason(str(e) or "车端不可达")
        return _ok(key, "move")

    # ---- 货叉升降（步骤18-20）----

    def _fork_cfg(self) -> dict:
        c = self._cfg.get("fork", {}) or {}
        return {
            "min_pos": int(c.get("min_pos", 75)),
            "max_pos": int(c.get("max_pos", 210)),
            "wait": int(c.get("wait", 20)),
            "tolerance": int(c.get("tolerance", 20)),
            "confirm_threshold": int(c.get("confirm_threshold", 100)),
        }

    async def _current_fork_height(self):
        """读当前叉高（mm）；失败返回 None。"""
        try:
            state = await self._jarvis.get_state()
        except Exception:
            return None
        fork_info = state.get("fork_info") if isinstance(state, dict) else None
        h = (fork_info or {}).get("fork_height")
        return h if isinstance(h, (int, float)) else None

    async def _handle_fork(self, intent: dict, ctx: dict) -> dict:
        cfg = self._fork_cfg()
        name = intent["name"]
        if name == "FORK_LIFT_UP":
            pos, key, direction = cfg["max_pos"], "fork_up", "升"
        elif name == "FORK_LIFT_DOWN":
            pos, key, direction = cfg["min_pos"], "fork_down", "降"
        else:  # FORK_LIFT_TO
            n = intent["slots"].get("n", cfg["min_pos"])
            pos = max(cfg["min_pos"], min(cfg["max_pos"], int(n)))
            key, direction = "fork_to", "调"

        # 安全确认：|目标-当前叉高| > confirm_threshold 时挂起待确认（ParamDialogue 接管）
        current = await self._current_fork_height()
        if current is not None and abs(pos - current) > cfg["confirm_threshold"]:
            self._dialogue.start_fork_confirm(ctx["client_id"], pos, key)
            return {
                "ok": True,
                "utterance_key": "confirm_fork",
                "utterance_params": {"direction": direction, "n": pos, "current": current},
                "speak_kind": "move",
                "speak_style": "ok",
                "awaiting": True,
            }
        return await self._exec_fork(pos, key)

    async def _exec_fork(self, pos: int, key: str) -> dict:
        cfg = self._fork_cfg()
        route = build_fork_route(pos, cfg["wait"], cfg["tolerance"])
        try:
            res = await self._jarvis.start_route(route)
        except Exception as e:
            return _fail_reason(str(e) or "车端不可达")
        if res and res.get("succeed") is False:
            return _fail_reason("路线下发失败")
        result = _ok(key, "move")
        if key == "fork_to":
            result["utterance_params"] = {"n": pos}
        return result

    # ---- TASK_* 任务（步骤27-30）----

    async def _handle_task(self, intent: dict, ctx: dict) -> dict:
        name = intent["name"]
        schema = TASK_SCHEMAS[name]
        slots = intent.get("slots") or {}
        if name == "TASK_FOLLOW_BACK":
            slots = resolve_station_fields(slots, await self._jarvis.path_point_names())
        # 参数装配：用户槽位优先，缺省用 schema 默认
        params = {}
        for pname, spec in schema["params"].items():
            if slots.get(pname) is not None:
                params[pname] = slots[pname]
            elif "default" in spec:
                params[pname] = spec["default"]
        missing = [
            pname
            for pname, spec in schema["params"].items()
            if spec.get("required") and params.get(pname) is None
        ]
        if missing:
            # 缺 safety/required 参数 → 启动追问（ParamDialogue collect 阶段）
            self._dialogue.start_task_collect(ctx["client_id"], name, params, missing, schema)
            return _speak(schema["params"][missing[0]]["ask"])
        if schema.get("confirm"):
            # 参数齐但属安全关键任务 → 汇总确认后执行
            summary = summary_for(name, params)
            self._dialogue.start_task_confirm(ctx["client_id"], name, params, summary)
            return {
                "ok": True,
                "utterance_key": "confirm_task",
                "utterance_params": {"summary": summary},
                "speak_kind": "move",
                "speak_style": "ok",
                "awaiting": True,
            }
        # head/get_pallet/charge：参数齐直接执行（confirm=False 策略，见 schemas 注释）
        return await self._exec_task(name, params)

    async def _exec_task(self, intent_name: str, params: dict) -> dict:
        schema = TASK_SCHEMAS[intent_name]
        if schema.get("route_cmd") == "follow_back":
            params = resolve_station_fields(params, await self._jarvis.path_point_names())
        try:
            route = build_route(schema["route_cmd"], params)
        except ValueError as e:
            return _fail_reason(str(e))
        try:
            res = await self._jarvis.start_route(route)
        except Exception as e:
            return _fail_reason(str(e) or "车端不可达")
        if res and res.get("succeed") is False:
            return _fail_reason("路线下发失败")
        result = _ok(schema["utterance"], "move")
        result["utterance_params"] = utterance_params_for(intent_name, params)
        return result

    async def _dispatch_exec(self, exec_payload: dict, ctx: dict) -> dict:
        """CONFIRM 后统一执行入口；fork/task 执行时仍要求持有现场锁（运动级约束）。
        flow 启动与 API 一致不要求现场锁（任务流与点动分级）。"""
        if exec_payload["kind"] == "flow":
            try:
                await self._flow_engine.start(exec_payload["flow_id"])
            except FlowBusyError:
                return _fail("flow_running", "fail_flow_running")
            except KeyError:
                return _fail_reason("任务流不存在")
            return {
                "ok": True,
                "utterance_key": "flow_started",
                "utterance_params": {"name": exec_payload.get("name", "")},
                "speak_kind": "move",
                "speak_style": "ok",
            }
        if not self._sessions.has_site(ctx["client_id"]):
            return _fail("no_site", "fail_no_site")
        if exec_payload["kind"] == "fork":
            return await self._exec_fork(exec_payload["pos"], exec_payload["key"])
        return await self._exec_task(exec_payload["intent"], exec_payload["slots"])

    # ---- 语音触发任务流（步骤48）----

    async def _handle_flow_start(self, intent: dict, ctx: dict) -> dict:
        name = (intent.get("slots") or {}).get("name") or ""
        engine = self._flow_engine
        store = self._flow_store
        if engine is None or store is None:
            return _fail_reason("任务流引擎未就绪")
        if engine.is_busy():
            return _fail("flow_running", "fail_flow_running")
        # 精确匹配优先，其次模糊匹配
        flow = await store.find_by_name(name)
        best = None
        if flow:
            best = {"id": flow["id"], "name": flow.get("name", "")}
        else:
            best, candidates = match_flow_name(name, await store.list())
            if best is None:
                return {
                    "ok": False,
                    "error_code": "flow_not_found",
                    "utterance_key": "fail_flow_not_found",
                    "utterance_params": {"name": name},
                    "speak_kind": "fail",
                    "speak_style": "fail",
                }
            if len(candidates) >= 2 and candidates[0]["score"] - candidates[1]["score"] < 0.15:
                return {
                    "ok": False,
                    "error_code": "flow_ambiguous",
                    "utterance_key": "fail_flow_ambiguous",
                    "utterance_params": {
                        "a": candidates[0]["name"],
                        "b": candidates[1]["name"],
                    },
                    "speak_kind": "fail",
                    "speak_style": "fail",
                }
        # 单候选 → 走 ParamDialogue 确认（复用 confirm 阶段机制）
        self._dialogue.start_flow_confirm(ctx["client_id"], best["id"], best["name"])
        return {
            "ok": True,
            "utterance_key": "confirm_flow",
            "utterance_params": {"name": best["name"]},
            "speak_kind": "move",
            "speak_style": "ok",
            "awaiting": True,
        }

    async def _handle_flow_ctl(self, action: str) -> dict:
        engine = self._flow_engine
        if engine is None:
            return _fail_reason("任务流引擎未就绪")
        if action == "pause":
            ok = await engine.pause()
        elif action == "resume":
            ok = await engine.resume()
        else:
            ok = await engine.cancel()
        if not ok:
            return _fail("flow_not_running", "flow_not_running")
        return _ok(f"flow_{action}d" if action != "cancel" else "flow_cancelled", "move")

    async def _handle_query(self, name: str) -> dict:
        try:
            state = await self._jarvis.get_state()
        except Exception:
            return _fail_reason("无法读取车况")
        if not isinstance(state, dict):
            state = {}

        if name == "QUERY_BATTERY":
            return {
                "ok": True,
                "utterance_key": "query_battery",
                "utterance_params": {"n": state.get("battery", 0) if state.get("battery") is not None else 0},
                "speak_kind": "query",
                "speak_style": "ok",
            }
        if name == "QUERY_MODE":
            # 真车 mode 为 JMode 名（Idle/ModeFocklift...），status 为 "mode,子状态" 组合串
            # （JWebService.cpp:137,154）——播 Mode 名的中文映射
            mode_raw = state.get("mode") or "未知"
            return {
                "ok": True,
                "utterance_key": "query_mode",
                "utterance_params": {"mode": _MODE_MAP.get(mode_raw, mode_raw)},
                "speak_kind": "query",
                "speak_style": "ok",
            }
        if name == "QUERY_POSE":
            pose = str(state.get("pose") or "0,0,0").split(",")
            return {
                "ok": True,
                "utterance_key": "query_pose",
                "utterance_params": {
                    "x": _js_round(_num(pose[0] if len(pose) > 0 else 0)),
                    "y": _js_round(_num(pose[1] if len(pose) > 1 else 0)),
                },
                "speak_kind": "query",
                "speak_style": "ok",
            }
        if name == "QUERY_FORK_HEIGHT":
            fork_info = state.get("fork_info") or {}
            n = fork_info.get("fork_height")
            return {
                "ok": True,
                "utterance_key": "query_fork",
                "utterance_params": {"n": n if n is not None else 0},
                "speak_kind": "query",
                "speak_style": "ok",
            }
        if name == "QUERY_MOTOR":
            return {
                "ok": True,
                "utterance_key": "query_motor",
                "utterance_params": {"state": "已使能" if state.get("motor") else "已断开"},
                "speak_kind": "query",
                "speak_style": "ok",
            }
        if name == "QUERY_ALARM":
            alarm = state.get("alarm") or "normal"
            return {
                "ok": True,
                "utterance_key": "query_alarm",
                "utterance_params": {"alarm": _ALARM_MAP.get(alarm, alarm)},
                "speak_kind": "query",
                "speak_style": "ok",
            }
        if name == "QUERY_SPEED":
            # state.speed（百分比）优先；缺失时退 vel 首项（m/s，一位小数）用另一条话术
            speed = state.get("speed")
            if isinstance(speed, (int, float)):
                return {
                    "ok": True,
                    "utterance_key": "query_speed",
                    "utterance_params": {"n": speed},
                    "speak_kind": "query",
                    "speak_style": "ok",
                }
            vel = str(state.get("vel") or "0").split(",")
            v_ms = round(_num(vel[0] if vel else 0), 1)
            return {
                "ok": True,
                "utterance_key": "query_speed_ms",
                "utterance_params": {"n": v_ms},
                "speak_kind": "query",
                "speak_style": "ok",
            }
        if name == "QUERY_TASK":
            current = state.get("current_routes") or {}
            routes_name = current.get("routes") if isinstance(current, dict) else None
            # 真车无任务时 routes="TEMP_DEFAULT"（SetDefaultRICK，见 engine._judge_node 注释）
            if routes_name and routes_name != "TEMP_DEFAULT":
                return {
                    "ok": True,
                    "utterance_key": "query_task",
                    "utterance_params": {
                        "routes": routes_name,
                        "status": current.get("status") or "unknown",
                    },
                    "speak_kind": "query",
                    "speak_style": "ok",
                }
            return _ok("query_task_none", "query")
        if name == "QUERY_ALARM_EXPLAIN":
            alarm = state.get("alarm") or "normal"
            explain = _ALARM_EXPLAIN.get(alarm) or f"当前告警：{alarm}"
            return {
                "ok": True,
                "utterance_key": "query_alarm_explain",
                "utterance_params": {"explain": explain},
                "speak_kind": "query",
                "speak_style": "ok",
            }
        if name == "QUERY_STATUS":
            # 车况聚合：电量 + 模式 + 当前任务 + 告警（task 文本在此预组，模板直接引用）
            battery = state.get("battery")
            mode_raw = state.get("mode") or "未知"
            current = state.get("current_routes") or {}
            routes_name = current.get("routes") if isinstance(current, dict) else None
            task = (
                f"正在执行{routes_name}"
                if routes_name and routes_name != "TEMP_DEFAULT"
                else "当前没有任务"
            )
            alarm = state.get("alarm") or "normal"
            return {
                "ok": True,
                "utterance_key": "query_status",
                "utterance_params": {
                    "n": battery if battery is not None else 0,
                    "mode": _MODE_MAP.get(mode_raw, mode_raw),
                    "task": task,
                    "alarm": _ALARM_MAP.get(alarm, alarm),
                },
                "speak_kind": "query",
                "speak_style": "ok",
            }
        return _fail("unknown", "fail_unknown")


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
