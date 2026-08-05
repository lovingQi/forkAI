"""FlowStore：FlowJSON 持久化（步骤36）。

- 每文件一个 {id}.json，存于 data_dir（默认 data/flows）
- 中文名索引启动时扫描建立、写时更新；find_by_name 精确匹配 + 去噪（norm）
- asyncio.Lock 线程安全
"""
import asyncio
import json
import re
import uuid
from pathlib import Path

from ..config import CORE_ROOT


def _denoise(name: str) -> str:
    return re.sub(r"[\s，,。.!！？?]", "", str(name or "")).lower()


class FlowStore:
    def __init__(self, data_dir: str | None = None):
        self._dir = Path(data_dir) if data_dir else CORE_ROOT / "data" / "flows"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._flows: dict[str, dict] = {}
        self._name_index: dict[str, str] = {}  # denoise(name) -> id
        self._load_all()

    def _load_all(self) -> None:
        for p in sorted(self._dir.glob("*.json")):
            try:
                flow = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            fid = flow.get("id") or p.stem
            flow["id"] = fid
            self._flows[fid] = flow
            self._name_index[_denoise(flow.get("name"))] = fid

    def _write(self, flow: dict) -> None:
        (self._dir / f"{flow['id']}.json").write_text(
            json.dumps(flow, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    async def create(self, flow: dict) -> str:
        async with self._lock:
            fid = flow.get("id") or uuid.uuid4().hex[:12]
            flow = dict(flow, id=fid)
            self._flows[fid] = flow
            self._name_index[_denoise(flow.get("name"))] = fid
            self._write(flow)
            return fid

    async def get(self, fid: str):
        async with self._lock:
            return self._flows.get(fid)

    async def update(self, fid: str, flow: dict) -> bool:
        async with self._lock:
            if fid not in self._flows:
                return False
            old = self._flows[fid]
            self._name_index.pop(_denoise(old.get("name")), None)
            flow = dict(flow, id=fid)
            self._flows[fid] = flow
            self._name_index[_denoise(flow.get("name"))] = fid
            self._write(flow)
            return True

    async def delete(self, fid: str) -> bool:
        async with self._lock:
            flow = self._flows.pop(fid, None)
            if not flow:
                return False
            self._name_index.pop(_denoise(flow.get("name")), None)
            try:
                (self._dir / f"{fid}.json").unlink()
            except OSError:
                pass
            return True

    async def list(self) -> list:
        """摘要：id/name/节点数。"""
        async with self._lock:
            return [
                {"id": f["id"], "name": f.get("name", ""), "nodes": len(f.get("nodes") or [])}
                for f in self._flows.values()
            ]

    @property
    def dir(self) -> Path:
        return self._dir

    async def find_by_name(self, name: str):
        """精确匹配优先，其次去噪匹配。"""
        async with self._lock:
            for f in self._flows.values():
                if f.get("name") == name:
                    return f
            fid = self._name_index.get(_denoise(name))
            return self._flows.get(fid) if fid else None
