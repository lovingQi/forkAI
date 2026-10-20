"""把配置、存储、引擎参数和 URDF 装到一个上下文里。"""
from pathlib import Path

from .config import resolve_under
from .engine.cartographer_profile import derive_product_profiles
from .ingest.preflight import engine_blockers
from .preview.live_hub import LiveHub
from .robot.urdf import load_urdf
from .scheduler import Scheduler
from .assets.map_store import MapStore
from .store.activity import ActivityLog
from .store.task_store import TaskStore


class Context:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        data = Path(cfg["dataDir"])
        data.mkdir(parents=True, exist_ok=True)
        self.tasks = TaskStore(data / "tasks")
        self.maps = MapStore(data / "maps")
        self.scheduler = Scheduler()
        self.live = LiveHub()
        self._activities: dict[str, ActivityLog] = {}
        self.sessions: dict = {}
        self.urdf_path = resolve_under(cfg, cfg["robot"]["urdf"])
        self.urdf = load_urdf(self.urdf_path) if self.urdf_path.is_file() else {"sha256": "", "links": set(), "joints": []}
        root = Path(cfg["engines"]["cartographer"]["profileRoot"])
        self.profiles = derive_product_profiles(root) if root.is_dir() else []

    def activity(self, task_id: str) -> ActivityLog:
        if task_id not in self._activities:
            self._activities[task_id] = ActivityLog(Path(self.cfg["dataDir"]) / "tasks" / task_id / "activity.jsonl")
        return self._activities[task_id]

    def status(self) -> dict:
        reasons = engine_blockers(self.cfg)
        eng = self.cfg["engines"]["cartographer"]
        available = not reasons
        return {
            "engines": [{
                "driver": "cartographer",
                "dimension": "2d",
                "available": available,
                "engineVersion": eng.get("engineVersion") or "",
                "buildId": eng.get("buildId") or "",
                "reason": "；".join(reasons),
            }],
            "capabilities": {
                "2d": {"online": available, "offline": False, "incremental": False},
                "3d": {"online": False, "offline": False, "incremental": False},
            },
            "activeTaskId": self.scheduler.active_task_id,
        }
