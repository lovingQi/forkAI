"""统一追踪：内存环形缓冲 + trace/mission 广播。

方法内部异常只打印，不影响主链路。
"""
import uuid
from collections import deque
from contextvars import ContextVar

from .config import now_ms

_CURRENT_TRACE: ContextVar[str | None] = ContextVar("forkai_trace_id", default=None)

_TERMINAL = {"succeeded", "failed", "cancelled"}


def _guard(fn):
    def wrap(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except Exception as e:
            print(f"[forkai-core] trace 异常: {e}", flush=True)
            return None

    return wrap


class Tracer:
    def __init__(self, bus, capacity: int = 500):
        self._bus = bus
        try:
            cap = int(capacity)
        except (TypeError, ValueError):
            cap = 500
        self._capacity = max(1, cap)
        self._buf: deque = deque(maxlen=self._capacity)
        self._seq = 0
        self._mission = None
        self._link_lost = False

    @property
    def capacity(self) -> int:
        return self._capacity

    @_guard
    def begin(self, kind: str, title: str, *, bind: bool = True) -> str:
        tid = uuid.uuid4().hex[:8]
        if bind:
            _CURRENT_TRACE.set(tid)
        return tid

    @_guard
    def bind(self, trace_id: str) -> None:
        if trace_id:
            _CURRENT_TRACE.set(trace_id)

    def current_id(self):
        try:
            return _CURRENT_TRACE.get()
        except Exception:
            return None

    @_guard
    def emit(self, cat, msg, *, status="info", data=None, dur_ms=None, trace_id=None):
        tid = trace_id if trace_id is not None else _CURRENT_TRACE.get()
        self._seq += 1
        entry = {
            "id": self._seq,
            "ts": now_ms(),
            "traceId": tid,
            "cat": str(cat),
            "msg": str(msg),
            "status": status,
            "durMs": dur_ms,
            "data": data,
        }
        self._buf.append(entry)
        if self._bus is not None:
            self._bus.broadcast("trace", entry)
        return entry

    def _same(self, trace_id) -> bool:
        return bool(self._mission) and self._mission.get("traceId") == trace_id

    def _broadcast_mission(self) -> None:
        if self._bus is not None and self._mission is not None:
            self._bus.broadcast("mission", self.current_mission())

    @_guard
    def mission_begin(self, trace_id, source, title, steps) -> None:
        if (
            source == "voice"
            and self._mission
            and self._mission.get("source") == "flow"
            and self._mission.get("status") in ("running", "paused")
        ):
            return
        self._mission = {
            "traceId": trace_id,
            "source": source,
            "title": title,
            "status": "running",
            "startedTs": now_ms(),
            "endedTs": None,
            "steps": [dict(s) for s in (steps or [])],
        }
        self._broadcast_mission()

    @_guard
    def mission_step(self, trace_id, step_id, status, note=None) -> None:
        if not self._same(trace_id):
            return
        for step in self._mission["steps"]:
            if step.get("id") == step_id:
                step["status"] = status
                if note is not None:
                    step["note"] = note
                self._broadcast_mission()
                return

    @_guard
    def mission_status(self, trace_id, status) -> None:
        if not self._same(trace_id):
            return
        self._mission["status"] = status
        if status in _TERMINAL:
            self._mission["endedTs"] = now_ms()
        self._broadcast_mission()

    @_guard
    def mission_sync(self, trace_id, step_statuses: dict, status: str) -> None:
        if not self._same(trace_id):
            return
        for step in self._mission["steps"]:
            sid = step.get("id")
            if sid in (step_statuses or {}):
                step["status"] = step_statuses[sid]
        self._mission["status"] = status
        if status in _TERMINAL:
            if not self._mission.get("endedTs"):
                self._mission["endedTs"] = now_ms()
        else:
            self._mission["endedTs"] = None
        self._broadcast_mission()

    @_guard
    def mission_insert(self, trace_id, after_step_id, step: dict) -> None:
        if not self._same(trace_id):
            return
        item = dict(step or {})
        item["inserted"] = True
        item.setdefault("note", None)
        steps = self._mission["steps"]
        for existing in steps:
            if existing.get("id") == item.get("id"):
                existing.update(item)
                existing["inserted"] = True
                self._broadcast_mission()
                return
        idx = next(
            (i for i, s in enumerate(steps) if s.get("id") == after_step_id),
            len(steps) - 1,
        )
        steps.insert(max(0, idx + 1), item)
        self._broadcast_mission()

    @_guard
    def history(self, limit: int) -> list:
        try:
            n = int(limit)
        except (TypeError, ValueError):
            n = self._capacity
        n = max(1, min(n, self._capacity))
        return list(self._buf)[-n:]

    @_guard
    def current_mission(self):
        if self._mission is None:
            return None
        return {
            **self._mission,
            "steps": [dict(s) for s in self._mission["steps"]],
        }

    @_guard
    def on_bus_event(self, type_: str, payload: dict) -> None:
        payload = payload or {}
        if type_ in ("trace", "mission"):
            return
        if type_ == "tts":
            engine = str(payload.get("ttsEngine") or "")
            if engine in ("cloud", "cloud-cache"):
                where = "cloud"
            elif engine == "piper":
                where = "local"
            else:
                where = "browser"
            self.emit(
                "SAY",
                str(payload.get("text") or ""),
                status="ok",
                data={
                    "metric": {
                        "kind": "tts",
                        "engine": engine,
                        "where": where,
                        "ok": True,
                    }
                },
            )
            return
        if type_ == "watchdog_stop":
            self.emit("GUARD", "看门狗：无指令自动停车", status="fail")
            return
        if type_ == "site_changed":
            if payload.get("reason") == "estop":
                self.emit("GUARD", "急停触发，强制退出现场", status="fail")
            elif payload.get("holderClientId"):
                self.emit("GUARD", "现场点动权已授予", status="ok")
            else:
                self.emit("GUARD", "现场点动权已释放", status="info")
            return
        if type_ == "wake_armed":
            self.emit("HEAR", "唤醒窗口已武装", status="info")
            return
        if type_ == "flow_jarvis_lost":
            if not self._link_lost:
                self._link_lost = True
                self.emit("LINK", "任务流轮询失败，车端失联", status="fail")
            return
        if type_ == "flow_jarvis_back":
            self._link_lost = False
            self.emit("LINK", "车端恢复，当前节点重发", status="ok")
            return
        if type_ == "flow_charge_full":
            self.emit(
                "GUARD",
                f"电量恢复到 {payload.get('battery')}%，等待人工继续",
                status="ok",
            )
