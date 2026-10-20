"""基本控制：按住行驶、避障开关、货叉步进、软件急停。"""
import asyncio
import math
import time

from ..config import now_ms
from ..jarvis.client import JarvisError
from ..jarvis.routes_builder import build_fork_extend_route, build_fork_route

# 符号与 executor 真车实测一致：前进左正右负；后退左负右正。
_DIRECTIONS = {
    "fwd": (1, 0),
    "back": (-1, 0),
    "fwd_left": (1, 1),
    "fwd_right": (1, -1),
    "back_left": (-1, -1),
    "back_right": (-1, 1),
    "spin_left": (0, 1),
    "spin_right": (0, -1),
}


class OpResult:
    def __init__(self, ok: bool = True, status: int = 200, error: str | None = None, data: dict | None = None):
        self.ok = ok
        self.status = status
        self.error = error
        self.data = data or {}


def _fail(status: int, error: str, **data) -> OpResult:
    return OpResult(False, status, error, data)


def _vel_moving(state: dict) -> bool:
    vel = state.get("vel")
    try:
        if isinstance(vel, str):
            parts = vel.split(",")
            vx = float(parts[0] or 0)
            vy = float(parts[1] or 0) if len(parts) > 1 else 0.0
        elif isinstance(vel, (list, tuple)) and vel:
            vx = float(vel[0] or 0)
            vy = float(vel[1] or 0) if len(vel) > 1 else 0.0
        else:
            return False
    except (TypeError, ValueError):
        return False
    return abs(vx) > 10 or abs(vy) > 10


class ConsoleController:
    def __init__(self, cfg: dict, sessions, jarvis, watchdog, bus, flow_engine):
        self._cfg = cfg
        self._sessions = sessions
        self._jarvis = jarvis
        self._watchdog = watchdog
        self._bus = bus
        self._flow = flow_engine
        console = cfg.get("console") or {}
        drive = console.get("drive") or {}
        avoid = console.get("avoid") or {}
        fork = cfg.get("fork") or {}
        extend = ((console.get("fork") or {}).get("extend") or {})
        self._min_trans = int(drive.get("minTrans", 100))
        self._default_trans = int(drive.get("defaultTrans", 500))
        self._max_trans = int(drive.get("maxTrans", 1000))
        self._step_trans = max(1, int(drive.get("stepTrans", 50)))
        self._unsafe_max = int(drive.get("unsafeMaxTrans", 300))
        self._restore_ms = int(avoid.get("autoRestoreMs", 5000))
        self._lift_min = int(fork.get("min_pos", 75))
        self._lift_max = int(fork.get("max_pos", 210))
        self._lift_step = int(fork.get("step", 50))
        self._lift_wait = int(fork.get("wait", 20))
        self._lift_tol = int(fork.get("tolerance", 20))
        self._confirm_threshold = int(fork.get("confirm_threshold", 100))
        self._ext_min = int(extend.get("min", 0))
        self._ext_max = int(extend.get("max", 640))
        self._ext_step = int(extend.get("step", 50))
        self._ext_wait = int(extend.get("wait", 20))
        self._ext_tol = int(extend.get("tolerance", 5))
        self._estop_latched = False
        self._avoid_enabled = True
        self._avoid_holder = None
        self._avoid_deadline = 0
        self._fork_busy = False
        self._fork_gen = 0
        self._guard: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def status(self) -> dict:
        return {
            "estopLatched": self._estop_latched,
            "avoidEnabled": self._avoid_enabled,
            "forkBusy": self._fork_busy,
            "chassisMoving": self._watchdog.is_moving(),
            "allowRemoteForce": self._sessions.remote_force_allowed(),
            "limits": {
                "trans": {
                    "min": self._min_trans,
                    "max": self._max_trans,
                    "step": self._step_trans,
                    "default": self._default_trans,
                    "unsafeMax": self._unsafe_max,
                },
                "rot": int(self._watchdog.rot_mag),
                "forkLift": {
                    "min": self._lift_min,
                    "max": self._lift_max,
                    "step": self._lift_step,
                    "confirmThreshold": self._confirm_threshold,
                },
                "forkExtend": {
                    "min": self._ext_min,
                    "max": self._ext_max,
                    "step": self._ext_step,
                    "tolerance": self._ext_tol,
                },
            },
        }

    def _emit(self) -> None:
        self._bus.broadcast("console_state", self.status())

    def _clip_trans(self, requested: int, avoid_on: bool) -> int:
        hi = self._max_trans if avoid_on else min(self._max_trans, self._unsafe_max)
        lo = self._min_trans
        step = self._step_trans
        try:
            value = int(requested)
        except (TypeError, ValueError):
            value = lo
        value = max(lo, min(hi, value))
        steps = int(math.floor((value - lo) / step + 0.5))
        snapped = lo + steps * step
        if snapped > hi:
            snapped = hi - ((hi - lo) % step)
        return max(lo, min(hi, snapped))

    async def _chassis_moving(self):
        if self._watchdog.is_moving():
            return True
        state = await self._jarvis.get_state()
        if isinstance(state, dict) and _vel_moving(state):
            return True
        return False

    async def _apply_safe(self, enabled: bool) -> None:
        res = await self._jarvis.control("safe", {"flag": bool(enabled)})
        if isinstance(res, dict) and res.get("succeed") is False:
            raise JarvisError(str(res.get("error") or "safe failed"))
        self._avoid_enabled = bool(enabled)
        if enabled:
            self._avoid_holder = None
            self._avoid_deadline = 0

    async def on_startup(self) -> None:
        try:
            await self._apply_safe(True)
        except Exception as e:
            print(f"[forkai-core] 启动恢复避障失败: {e}", flush=True)
            self._avoid_enabled = True
        self._guard = asyncio.create_task(self._guard_loop())

    async def shutdown(self) -> None:
        if self._guard:
            self._guard.cancel()
            self._guard = None

    async def on_alarm_estop(self) -> None:
        async with self._lock:
            self._fork_gen += 1
            self._fork_busy = False
            try:
                await self._apply_safe(True)
            except Exception as e:
                print(f"[forkai-core] 急停时恢复避障失败: {e}", flush=True)
            self._emit()

    async def _guard_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(0.5)
                async with self._lock:
                    if self._avoid_enabled:
                        continue
                    holder = self._sessions.get_site_holder()
                    timed_out = now_ms() >= self._avoid_deadline > 0
                    stolen = holder != self._avoid_holder
                    if not (timed_out or stolen):
                        continue
                    try:
                        await self._apply_safe(True)
                    except Exception as e:
                        print(f"[forkai-core] 自动恢复避障失败: {e}", flush=True)
                        continue
                    self._emit()
        except asyncio.CancelledError:
            pass

    async def drive(self, client_id: str, direction: str, trans, start: bool = False) -> OpResult:
        async with self._lock:
            if not self._sessions.has_site(client_id):
                return _fail(403, "no_site")
            if self._flow is not None and self._flow.is_busy():
                return _fail(409, "flow_running")
            if self._estop_latched:
                return _fail(409, "estop")
            if self._fork_busy:
                return _fail(409, "fork_busy")
            spec = _DIRECTIONS.get(str(direction or ""))
            if spec is None:
                return _fail(400, "invalid_direction")
            if start:
                try:
                    state = await self._jarvis.get_state()
                except JarvisError as e:
                    return _fail(502, str(e))
                if isinstance(state, dict) and (state.get("alarm") == "estop" or state.get("motor") is False):
                    return _fail(409, "estop")
            sign, rot_sign = spec
            magnitude = self._default_trans if trans is None else trans
            try:
                magnitude = int(magnitude)
            except (TypeError, ValueError):
                return _fail(400, "invalid_params")
            if sign == 0:
                trans_out = 0
            else:
                trans_out = sign * self._clip_trans(abs(magnitude), self._avoid_enabled)
            rot_out = rot_sign * int(self._watchdog.rot_mag)
            try:
                await self._watchdog.drive(trans_out, rot_out)
            except JarvisError as e:
                return _fail(502, str(e))
            except Exception as e:
                return _fail(502, str(e) or "drive failed")
            if not self._avoid_enabled:
                self._avoid_deadline = now_ms() + self._restore_ms
                self._avoid_holder = client_id
            self._emit()
            return OpResult(data={"trans": trans_out, "rot": rot_out})

    async def drive_stop(self, client_id: str) -> OpResult:
        async with self._lock:
            if not self._sessions.has_site(client_id):
                return _fail(403, "no_site")
            try:
                await self._watchdog.stop(False)
            except JarvisError as e:
                return _fail(502, str(e))
            if not self._avoid_enabled:
                try:
                    await self._apply_safe(True)
                except JarvisError as e:
                    return _fail(502, str(e))
            self._emit()
            return OpResult()

    async def set_avoid(self, client_id: str, enabled: bool, confirm: bool) -> OpResult:
        async with self._lock:
            if not self._sessions.has_site(client_id):
                return _fail(403, "no_site")
            try:
                moving = await self._chassis_moving()
            except JarvisError as e:
                return _fail(502, str(e))
            if moving:
                return _fail(409, "robot_moving")
            if not enabled and not confirm:
                return _fail(400, "confirm_required")
            try:
                await self._apply_safe(bool(enabled))
            except JarvisError as e:
                return _fail(502, str(e))
            if not enabled:
                self._avoid_holder = client_id
                self._avoid_deadline = now_ms() + self._restore_ms
            self._emit()
            return OpResult()

    async def fork(self, client_id: str, axis: str, mode: str, delta=None, pos=None, confirm: bool = False) -> OpResult:
        async with self._lock:
            if not self._sessions.has_site(client_id):
                return _fail(403, "no_site")
            if self._estop_latched:
                return _fail(409, "estop")
            if self._fork_busy:
                return _fail(409, "fork_busy")
            try:
                moving = await self._chassis_moving()
                state = await self._jarvis.get_state()
            except JarvisError as e:
                return _fail(502, str(e))
            if moving:
                return _fail(409, "chassis_moving")
            info = state.get("fork_info") if isinstance(state, dict) else None
            if not isinstance(info, dict):
                info = {}
            if axis == "lift":
                current = info.get("fork_height")
                current = int(current) if isinstance(current, (int, float)) else 0
                lo, hi = self._lift_min, self._lift_max
                step = self._lift_step
                wait, tol = self._lift_wait, self._lift_tol
            elif axis == "extend":
                if "fork_cur_dis" not in info:
                    return _fail(400, "not_supported")
                current = int(info.get("fork_cur_dis") or 0)
                reported = info.get("fork_max_dis")
                cap = int(reported) if isinstance(reported, (int, float)) else self._ext_max
                lo, hi = self._ext_min, min(self._ext_max, cap)
                step = self._ext_step
                wait, tol = self._ext_wait, self._ext_tol
            else:
                return _fail(400, "invalid_params")
            if mode == "step":
                try:
                    delta_i = int(delta)
                except (TypeError, ValueError):
                    return _fail(400, "invalid_params")
                if delta_i == 0:
                    return _fail(400, "invalid_params")
                target = current + delta_i
            elif mode == "target":
                try:
                    target = int(pos)
                except (TypeError, ValueError):
                    return _fail(400, "invalid_params")
            else:
                return _fail(400, "invalid_params")
            target = max(lo, min(hi, target))
            if abs(target - current) > self._confirm_threshold and not confirm:
                return _fail(400, "confirm_required", current=current, target=target)
            if target == current:
                return OpResult(data={"current": current, "target": target, "busy": False})
            route = (
                build_fork_route(target, wait, tol, prefix="console")
                if axis == "lift"
                else build_fork_extend_route(target, wait, tol)
            )
            try:
                res = await self._jarvis.start_route(route)
            except JarvisError as e:
                return _fail(502, str(e))
            if isinstance(res, dict) and res.get("succeed") is False:
                return _fail(502, str(res.get("error") or "route rejected"))
            self._fork_gen += 1
            gen = self._fork_gen
            self._fork_busy = True
            self._emit()
            asyncio.create_task(self._poll_fork(gen, axis, target, tol, wait))
            return OpResult(data={"current": current, "target": target, "busy": True})

    async def _poll_fork(self, gen: int, axis: str, target: int, tol: int, wait_s: int) -> None:
        key = "fork_height" if axis == "lift" else "fork_cur_dis"
        deadline = time.monotonic() + max(1, int(wait_s))
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(0.5)
                if gen != self._fork_gen:
                    return
                try:
                    state = await self._jarvis.get_state()
                except Exception:
                    continue
                info = state.get("fork_info") if isinstance(state, dict) else None
                cur = (info or {}).get(key) if isinstance(info, dict) else None
                if isinstance(cur, (int, float)) and abs(float(cur) - target) <= tol:
                    break
        finally:
            async with self._lock:
                if gen == self._fork_gen:
                    self._fork_busy = False
                    self._emit()

    async def soft_stop(self, _client_id: str) -> OpResult:
        async with self._lock:
            err = await self._halt_motion(restore_avoid=True)
            self._emit()
            if err:
                return _fail(502, err)
            return OpResult()

    async def estop(self, client_id: str) -> OpResult:
        async with self._lock:
            self._estop_latched = True
            err = await self._halt_motion(restore_avoid=True)
            if self._flow is not None and self._flow.is_busy():
                try:
                    await self._flow.cancel()
                except Exception as e:
                    print(f"[forkai-core] 急停取消任务流失败: {e}", flush=True)
            try:
                res = await self._jarvis.control("motor", {"flag": False})
                if isinstance(res, dict) and res.get("succeed") is False:
                    err = err or str(res.get("error") or "motor off failed")
            except JarvisError as e:
                err = err or str(e)
            self._bus.broadcast("console_estop", {"by": client_id})
            self._emit()
            if err:
                return _fail(502, err)
            return OpResult()

    async def estop_release(self, client_id: str, confirm: bool) -> OpResult:
        async with self._lock:
            if not self._sessions.has_site(client_id):
                return _fail(403, "no_site")
            if not confirm:
                return _fail(400, "confirm_required")
            try:
                res = await self._jarvis.control("motor", {"flag": True})
            except JarvisError as e:
                return _fail(502, str(e))
            if isinstance(res, dict) and res.get("succeed") is False:
                return _fail(502, str(res.get("error") or "motor on failed"))
            self._estop_latched = False
            self._emit()
            return OpResult()

    async def _halt_motion(self, restore_avoid: bool) -> str | None:
        err = None
        self._fork_gen += 1
        self._fork_busy = False
        try:
            await self._watchdog.stop(False)
        except Exception as e:
            err = str(e) or "stop failed"
        if restore_avoid and not self._avoid_enabled:
            try:
                await self._apply_safe(True)
            except Exception as e:
                err = err or str(e)
        return err
