"""ParamDialogue：TASK_* 参数追问 + 汇总确认（步骤30）。

按 clientId 隔离的多轮对话挂起态，统一接管：
- TASK_* 缺 safety/required 参数 → 依次追问（stage=collect）→ 集齐 → 汇总确认（stage=confirm）
- 参数齐但 schema.confirm=True → 直接进汇总确认
- 货叉安全确认（原 PendingStore 行为，迁移至此，对外行为不变）

会话：{"kind": "task"|"fork", "stage": "collect"|"confirm", "intent", "slots",
       "missing", "schema", "exec", "summary", "activity"}
超时：30s 无应答自动清（惰性检查，下一次交互时发现；晚到的 CONFIRM/CANCEL
得到 timeout_cancel 话术）；硬 TTL 120s 兜底。
任何轮次说"取消"放弃；说"停止"放弃并执行停车。
"""
import re
import time

from ..nlu.rules import norm

IDLE_TIMEOUT_MS = 30_000
HARD_TTL_MS = 120_000


def _now_ms() -> int:
    return int(time.time() * 1000)


def _extract_value(spec: dict, text: str):
    """从用户回答提取参数值。返回 (ok, value)。"""
    ptype = spec.get("type", "name")
    if ptype == "number":
        m = re.search(r"-?\d+(?:\.\d+)?", text)
        if not m:
            return False, None
        v = float(m.group(0))
        return True, int(v) if v == int(v) else v
    # name：取原文过 norm；剥引导介词与尾部「站点/点/语气词」，保留「区」。
    # 大小写下发前按地图 PathPoint 匹配。
    v = norm(text)
    v = re.sub(r"^(从|自|到|去|往)+", "", v)
    v = re.sub(r"(站点|吧|啊|呀|呢)$", "", v)
    if v.endswith("点") and len(v) > 1:
        v = v[:-1]
    return (bool(v), v or None)


class ParamDialogue:
    def __init__(self, idle_timeout_ms: int = IDLE_TIMEOUT_MS, ttl_ms: int = HARD_TTL_MS):
        self._idle_ms = idle_timeout_ms
        self._ttl_ms = ttl_ms
        self._sessions: dict[str, dict] = {}

    # ---- 会话生命周期 ----
    def _put(self, client_id: str, session: dict) -> None:
        session["activity"] = _now_ms()
        session["created"] = _now_ms()
        self._sessions[client_id] = session

    def start_task_collect(self, client_id: str, intent: str, slots: dict,
                           missing: list, schema: dict) -> None:
        self._put(client_id, {
            "kind": "task", "stage": "collect", "intent": intent,
            "slots": dict(slots), "missing": list(missing), "schema": schema,
        })

    def start_task_confirm(self, client_id: str, intent: str, slots: dict, summary: str) -> None:
        self._put(client_id, {
            "kind": "task", "stage": "confirm", "intent": intent,
            "slots": dict(slots), "missing": [], "summary": summary,
            "exec": {"kind": "task", "intent": intent, "slots": dict(slots)},
        })

    def start_fork_confirm(self, client_id: str, pos: int, key: str) -> None:
        self._put(client_id, {
            "kind": "fork", "stage": "confirm",
            "exec": {"kind": "fork", "pos": pos, "key": key},
        })

    def start_flow_confirm(self, client_id: str, flow_id: str, name: str) -> None:
        """任务流启动确认（步骤48）：CONFIRM 后由 executor._dispatch_exec 执行 engine.start。"""
        self._put(client_id, {
            "kind": "flow", "stage": "confirm",
            "exec": {"kind": "flow", "flow_id": flow_id, "name": name},
        })

    def _expired(self, session: dict) -> bool:
        now = _now_ms()
        return (now - session["activity"] > self._idle_ms) or (
            now - session["created"] > self._ttl_ms
        )

    def current(self, client_id: str):
        """取会话；过期则清除并返回 None（超时惰性清理）。"""
        session = self._sessions.get(client_id)
        if not session:
            return None
        if self._expired(session):
            del self._sessions[client_id]
            return None
        return session

    def check_timeout(self, client_id: str) -> bool:
        """存在过期的挂起会话 → 清除并返回 True（用于给晚到应答回 timeout_cancel）。"""
        session = self._sessions.get(client_id)
        if session and self._expired(session):
            del self._sessions[client_id]
            return True
        return False

    def touch(self, client_id: str) -> None:
        if client_id in self._sessions:
            self._sessions[client_id]["activity"] = _now_ms()

    def cancel(self, client_id: str) -> bool:
        return self._sessions.pop(client_id, None) is not None

    def pop(self, client_id: str):
        session = self._sessions.pop(client_id, None)
        if not session or self._expired(session):
            return None
        return session

    # ---- 参数续答 ----
    def feed_collect(self, client_id: str, text: str) -> dict:
        """collect 阶段喂入用户回答。返回:
        {"action": "ask"|"reask", "prompt": 追问话术}
        {"action": "confirm", "summary": 汇总文本}
        """
        session = self._sessions[client_id]
        schema = session["schema"]
        missing = session["missing"]
        pname = missing[0]
        spec = schema["params"][pname]
        ok, value = _extract_value(spec, text)
        if not ok:
            return {"action": "reask", "prompt": spec["ask"]}
        session["slots"][pname] = value
        missing.pop(0)
        self.touch(client_id)
        if missing:
            nxt = missing[0]
            return {"action": "ask", "prompt": schema["params"][nxt]["ask"]}
        # 集齐 → 进汇总确认
        from .schemas import summary_for

        session["stage"] = "confirm"
        session["exec"] = {
            "kind": "task",
            "intent": session["intent"],
            "slots": dict(session["slots"]),
        }
        summary = summary_for(session["intent"], session["slots"])
        session["summary"] = summary
        return {"action": "confirm", "summary": summary}
