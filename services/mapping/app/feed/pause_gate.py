"""离线送数的暂停门。暂停时等待，不丢已经读到的数据。"""
import asyncio


class PauseGate:
    def __init__(self):
        self._paused = asyncio.Event()
        self._paused.set()

    def pause(self) -> bool:
        if not self._paused.is_set():
            return False
        self._paused.clear()
        return True

    def resume(self) -> bool:
        if self._paused.is_set():
            return False
        self._paused.set()
        return True

    async def wait(self) -> None:
        await self._paused.wait()
