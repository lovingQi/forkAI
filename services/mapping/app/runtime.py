"""把配置、存储、引擎参数和 URDF 装到一个上下文里。"""
import json
from pathlib import Path

from .config import resolve_under
from .engine.cartographer_profile import derive_product_profiles
from .ingest.preflight import engine_blockers
from .ingest.preflight3d import traverse_blockers
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
        self._traverse_manifest = self._read_traverse_manifest()
        if not traverse_blockers(cfg):
            traverse = cfg["engines"]["traverse"]
            inputs = traverse.get("inputs") or {}
            self.profiles.append({
                "id": "lidar-inertial-3d",
                "name": "在线 3D（雷达 + IMU）",
                "dimension": "3d",
                "pipeline": "lidar-inertial-3d",
                "runtimeRevision": self._traverse_manifest.get("profileRevision") or "1",
                "inputs": {
                    "lidar": {"sensorId": "lidar", "topic": inputs.get("lidar") or ""},
                    "imu": {"sensorId": "imu", "topic": inputs.get("imu") or ""},
                },
            })
        from .recording.store import RecordingStore
        self.recordings = RecordingStore(data / "recordings")
        self.recorder = None

    def _read_traverse_manifest(self) -> dict:
        raw = str(((self.cfg.get("engines") or {}).get("traverse") or {}).get("root") or "")
        path = Path(raw) / "runtime.json" if raw else None
        if path is None or not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _traverse_caps(self) -> dict:
        ready = not traverse_blockers(self.cfg)
        return {"online": ready, "offline": ready, "incremental": ready}

    def activity(self, task_id: str) -> ActivityLog:
        if task_id not in self._activities:
            self._activities[task_id] = ActivityLog(Path(self.cfg["dataDir"]) / "tasks" / task_id / "activity.jsonl")
        return self._activities[task_id]

    def status(self) -> dict:
        reasons = engine_blockers(self.cfg)
        eng = self.cfg["engines"]["cartographer"]
        available = not reasons
        traverse_reasons = traverse_blockers(self.cfg)
        traverse_ready = not traverse_reasons
        manifest = self._traverse_manifest
        return {
            "engines": [
                {
                    "driver": "cartographer",
                    "dimension": "2d",
                    "available": available,
                    "engineVersion": eng.get("engineVersion") or "",
                    "buildId": eng.get("buildId") or "",
                    "reason": "；".join(reasons),
                },
                {
                    "driver": "traverse",
                    "dimension": "3d",
                    "available": traverse_ready,
                    "engineVersion": manifest.get("engineVersion") or "",
                    "buildId": manifest.get("buildId") or "",
                    "reason": "；".join(traverse_reasons),
                },
            ],
            "capabilities": {
                "2d": {"online": available, "offline": available, "incremental": available},
                "3d": self._traverse_caps(),
            },
            "activeTaskId": self.scheduler.active_task_id,
        }
