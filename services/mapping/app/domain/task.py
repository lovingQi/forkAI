"""任务状态。取值与 robospace MAPPING_TASK_STATUSES 一致。"""
from dataclasses import dataclass, field

STATUSES = (
    "draft",
    "validating",
    "queued",
    "starting",
    "running",
    "paused",
    "ready-to-save",
    "finishing",
    "exporting",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
)
TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})
ALLOWED = {
    "draft": {"validating"},
    "validating": {"starting", "failed"},
    "queued": {"starting", "failed", "cancelled"},
    "starting": {"running", "failed", "cancelled"},
    "running": {"ready-to-save", "finishing", "failed", "cancelled", "interrupted"},
    "paused": {"running", "finishing", "failed", "cancelled"},
    "ready-to-save": {"finishing", "failed", "cancelled", "interrupted"},
    "finishing": {"exporting", "failed"},
    "exporting": {"completed", "failed"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
    "interrupted": set(),
}


def can_transition(src: str, dst: str) -> bool:
    return dst in ALLOWED.get(src, set())


@dataclass
class MappingTask:
    id: str
    dimension: str
    mode: str
    name: str
    profileId: str
    profileRevision: str
    inputs: dict
    snapshot: dict = field(default_factory=dict)
    status: str = "validating"
    stopReason: str | None = None
    stats: dict = field(default_factory=lambda: {
        "received": 0, "accepted": 0, "submitted": 0, "rejected": 0, "pending": 0, "droppedRanges": 0,
    })
    error: dict | None = None
    projectId: str | None = None
    createdAt: str = ""
    updatedAt: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "dimension": self.dimension,
            "mode": self.mode,
            "name": self.name,
            "profileId": self.profileId,
            "profileRevision": self.profileRevision,
            "inputs": self.inputs,
            "snapshot": self.snapshot,
            "status": self.status,
            "stopReason": self.stopReason,
            "stats": self.stats,
            "error": self.error,
            "projectId": self.projectId,
            "createdAt": self.createdAt,
            "updatedAt": self.updatedAt,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "MappingTask":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})
