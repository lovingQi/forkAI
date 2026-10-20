"""活动事件。诊断只留最近 20 条。序号有缺口时让客户端重置游标。"""
import json
from datetime import datetime, timezone
from pathlib import Path


class ActivityLog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events: list[dict] = []
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.events.append(json.loads(line))

    @property
    def latest(self) -> int:
        return self.events[-1]["sequence"] if self.events else 0

    def append(self, kind: str, data: dict) -> int:
        seq = self.latest + 1
        event = {
            "sequence": seq,
            "time": datetime.now(timezone.utc).isoformat(),
            "type": kind,
            "data": data,
        }
        self.events.append(event)
        if kind == "diagnostic":
            diags = [e for e in self.events if e["type"] == "diagnostic"]
            if len(diags) > 20:
                drop = {id(e) for e in diags[:-20]}
                self.events = [e for e in self.events if id(e) not in drop]
                self._rewrite()
                return seq
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return seq

    def read(self, after: int) -> dict:
        if after < 0:
            after = 0
        gap = False
        if after and self.events:
            known = {e["sequence"] for e in self.events}
            gap = after not in known and after != 0
        events = [e for e in self.events if e["sequence"] > after]
        reset = bool(gap and not events)
        if reset:
            events = list(self.events)
        return {"events": events, "latestSequence": self.latest, "reset": reset}

    def _rewrite(self) -> None:
        text = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in self.events)
        tmp = self.path.with_suffix(".jsonl.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.path)
