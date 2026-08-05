"""MotionWatchdog：2s 无指令自动停（1:1 对齐 watchdog.ts，asyncio.TimerHandle 实现）。

drive 后重置定时器；超时自动调 jarvis stop 并触发 on_stop（用于 broadcast watchdog_stop）。
"""
import asyncio
import math


class MotionWatchdog:
    def __init__(self, jarvis, watchdog_ms: int, max_speed: int, default_speed: int, on_stop=None):
        self._jarvis = jarvis
        self._watchdog_ms = watchdog_ms
        self._max_speed = max_speed
        self._on_stop = on_stop
        self.speed = default_speed
        self._timer: asyncio.TimerHandle | None = None
        self._moving = False

    def set_speed(self, v) -> None:
        # JS Math.round（半进一），速度恒为正
        self.speed = max(5, min(self._max_speed, int(math.floor(float(v) + 0.5))))

    def bump_speed(self, delta: int) -> None:
        self.set_speed(self.speed + delta)

    async def drive(self, trans: int, rot: int) -> None:
        await self._jarvis.control(
            "drive", {"trans": trans, "rot": rot, "speed": self.speed}
        )
        self._moving = True
        self._reset_timer()

    async def stop(self, from_watchdog: bool = False) -> None:
        self._clear_timer()
        self._moving = False
        await self._jarvis.control("stop")
        if from_watchdog and self._on_stop:
            self._on_stop()

    def _reset_timer(self) -> None:
        self._clear_timer()
        loop = asyncio.get_running_loop()
        self._timer = loop.call_later(self._watchdog_ms / 1000.0, self._on_timeout)

    def _on_timeout(self) -> None:
        self._timer = None
        asyncio.ensure_future(self._stop_safely())

    async def _stop_safely(self) -> None:
        try:
            await self.stop(True)
        except Exception:
            pass

    def _clear_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def is_moving(self) -> bool:
        return self._moving
