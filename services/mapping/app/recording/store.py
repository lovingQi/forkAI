"""录包目录：data/recordings/<id>/recording.mcap + meta.json。"""
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RecordingStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, name: str, dimension: str, source: str, topics: list[dict]) -> dict:
        rec_id = str(uuid.uuid4())
        folder = self.root / rec_id
        folder.mkdir(parents=True)
        meta = {
            "id": rec_id,
            "name": name,
            "dimension": dimension,
            "source": source,
            "status": "recording",
            "topics": topics,
            "createdAt": _now(),
            "bytes": 0,
            "startTimeNs": None,
            "endTimeNs": None,
            "format": "mcap",
            "path": str(folder / "recording.mcap"),
        }
        self._write(folder / "meta.json", meta)
        return meta

    def list(self) -> list[dict]:
        rows = []
        for child in self.root.iterdir():
            meta_path = child / "meta.json"
            if meta_path.is_file():
                rows.append(json.loads(meta_path.read_text(encoding="utf-8")))
        rows.sort(key=lambda item: item.get("createdAt") or "", reverse=True)
        return rows

    def get(self, rec_id: str) -> dict | None:
        path = self._dir(rec_id) / "meta.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, meta: dict) -> None:
        self._write(self._dir(meta["id"]) / "meta.json", meta)

    def delete(self, rec_id: str) -> None:
        shutil.rmtree(self._dir(rec_id))

    def _dir(self, rec_id: str) -> Path:
        if not rec_id or "/" in rec_id or rec_id.startswith("."):
            raise FileNotFoundError(rec_id)
        path = (self.root / rec_id).resolve()
        if path.parent != self.root.resolve() or not path.is_dir():
            raise FileNotFoundError(rec_id)
        return path

    def _write(self, path: Path, meta: dict) -> None:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
