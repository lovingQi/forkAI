"""车况差量观察：按配置间隔读 get_state，只记录有意义的变化。"""
import asyncio

from .executor import _ALARM_MAP, _MODE_MAP

_IDLE_ROUTES = ("", "TEMP_DEFAULT", None)


class StateObserver:
    def __init__(self, cfg: dict, jarvis, tracer):
        self._jarvis = jarvis
        self._tracer = tracer
        tf = (cfg or {}).get("trace") or {}
        try:
            self._poll_ms = int(tf.get("observe_poll_ms", 1000) or 0)
        except (TypeError, ValueError):
            self._poll_ms = 1000
        self._task: asyncio.Task | None = None
        self._baseline = True
        self._prev: dict = {}
        self._fail_streak = 0
        self._lost = False

    async def start(self) -> None:
        if self._poll_ms <= 0 or self._tracer is None:
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def _snap(self, state: dict) -> dict:
        cr = state.get("current_routes") or {}
        routes = cr.get("routes") if isinstance(cr, dict) else None
        return {
            "mode": state.get("mode"),
            "routes": routes,
            "alarm": state.get("alarm") or "normal",
            "battery": state.get("battery"),
            "charing": state.get("charing"),
        }

    def _emit(self, cat, msg, status="info") -> None:
        try:
            self._tracer.emit(cat, msg, status=status)
        except Exception as e:
            print(f"[forkai-core] trace 异常: {e}", flush=True)

    def _diff(self, state: dict) -> None:
        cur = self._snap(state)
        prev = self._prev
        if cur["mode"] != prev.get("mode"):
            a = _MODE_MAP.get(prev.get("mode") or "", prev.get("mode") or "未知")
            b = _MODE_MAP.get(cur["mode"] or "", cur["mode"] or "未知")
            self._emit("OBS", f"模式 {a} → {b}")
        prev_route, cur_route = prev.get("routes"), cur["routes"]
        if prev_route in _IDLE_ROUTES and cur_route not in _IDLE_ROUTES and cur_route:
            self._emit("TASK", f"车端路线 {cur_route} 开始", "running")
        if prev_route not in _IDLE_ROUTES and prev_route and cur_route == "TEMP_DEFAULT":
            fork_info = state.get("fork_info") if isinstance(state.get("fork_info"), dict) else {}
            h = fork_info.get("fork_height")
            extra = f" · 叉高 {h}mm" if isinstance(h, (int, float)) else ""
            self._emit("TASK", f"车端路线 {prev_route} 结束{extra}")
        if cur["alarm"] != prev.get("alarm"):
            alarm = cur["alarm"] or "normal"
            if alarm != "normal":
                self._emit("GUARD", f"告警：{_ALARM_MAP.get(alarm, alarm)}", "fail")
            else:
                self._emit("GUARD", "告警解除", "ok")
        pb, cb = prev.get("battery"), cur["battery"]
        if isinstance(pb, (int, float)) and isinstance(cb, (int, float)):
            if int(pb) // 10 != int(cb) // 10:
                self._emit("OBS", f"电量 {int(cb)}%")
        if bool(cur["charing"]) != bool(prev.get("charing")):
            self._emit("OBS", "开始充电" if cur["charing"] else "停止充电")
        self._prev = cur

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._poll_ms / 1000.0)
                try:
                    state = await self._jarvis.get_state()
                except Exception:
                    self._fail_streak += 1
                    if self._fail_streak >= 3 and not self._lost:
                        self._lost = True
                        self._emit("LINK", "车端不可达", "fail")
                    continue
                if self._lost:
                    self._lost = False
                    self._emit("LINK", "车端连接恢复", "ok")
                self._fail_streak = 0
                if not isinstance(state, dict):
                    continue
                if self._baseline:
                    self._prev = self._snap(state)
                    self._baseline = False
                    continue
                try:
                    self._diff(state)
                except Exception as e:
                    print(f"[forkai-core] trace 异常: {e}", flush=True)
        except asyncio.CancelledError:
            pass
