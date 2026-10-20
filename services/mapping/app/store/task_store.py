"""任务 JSON：先写临时文件再 replace。"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ..domain.task import TERMINAL, MappingTask


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, task_id: str) -> Path:
        return self.root / task_id / "task.json"

    def save(self, task: MappingTask) -> None:
        task.updatedAt = _now()
        if not task.createdAt:
            task.createdAt = task.updatedAt
        dest = self.path(task.id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".json.tmp")
        data = json.dumps(task.to_dict(), ensure_ascii=False, indent=2)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dest)

    def load(self, task_id: str) -> MappingTask | None:
        dest = self.path(task_id)
        if not dest.is_file():
            return None
        return MappingTask.from_dict(json.loads(dest.read_text(encoding="utf-8")))

    def list_tasks(self) -> list[MappingTask]:
        out = []
        if not self.root.is_dir():
            return out
        for child in self.root.iterdir():
            if (child / "task.json").is_file():
                task = self.load(child.name)
                if task:
                    out.append(task)
        return out

    def mark_interrupted_on_boot(self) -> int:
        count = 0
        for task in self.list_tasks():
            if task.status not in TERMINAL:
                task.status = "interrupted"
                task.stopReason = "service_restarted"
                self.save(task)
                count += 1
        return count
