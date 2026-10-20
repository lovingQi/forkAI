"""一次只允许一个建图任务。"""
import asyncio

from .errors import MappingError


class Scheduler:
    def __init__(self):
        self._lock = asyncio.Lock()
        self.active_task_id: str | None = None

    async def acquire(self, task_id: str) -> None:
        async with self._lock:
            if self.active_task_id and self.active_task_id != task_id:
                raise MappingError("MAPPING_TASK_BUSY", "已有建图任务在进行")
            self.active_task_id = task_id

    async def release(self, task_id: str) -> None:
        async with self._lock:
            if self.active_task_id == task_id:
                self.active_task_id = None
