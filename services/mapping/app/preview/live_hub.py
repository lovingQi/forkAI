"""每个通道只保留最新一帧，新连接立刻补发。"""
import json
import struct


class LiveHub:
    def __init__(self):
        self._sockets: dict[str, set] = {}
        self._latest: dict[str, dict[str, str | bytes]] = {}

    def open(self, task_id: str) -> None:
        self._sockets.setdefault(task_id, set())
        self._latest.setdefault(task_id, {})

    def close(self, task_id: str) -> None:
        for ws in list(self._sockets.get(task_id, ())):
            self._sockets[task_id].discard(ws)
        self._sockets.pop(task_id, None)
        self._latest.pop(task_id, None)

    async def attach(self, task_id: str, ws) -> None:
        self.open(task_id)
        self._sockets[task_id].add(ws)
        for payload in self._latest.get(task_id, {}).values():
            await self._send(ws, payload)

    def detach(self, task_id: str, ws) -> None:
        self._sockets.get(task_id, set()).discard(ws)

    async def publish_text(self, task_id: str, channel: str, obj: dict) -> None:
        self.open(task_id)
        payload = json.dumps(obj, ensure_ascii=False)
        self._latest[task_id][channel] = payload
        await self._fanout(task_id, payload)

    async def publish_map(self, task_id: str, header: dict, png: bytes) -> None:
        self.open(task_id)
        head = json.dumps(header, ensure_ascii=False).encode("utf-8")
        payload = struct.pack("<I", len(head)) + head + png
        self._latest[task_id]["map"] = payload
        await self._fanout(task_id, payload)

    async def _fanout(self, task_id: str, payload) -> None:
        dead = []
        for ws in list(self._sockets.get(task_id, ())):
            try:
                await self._send(ws, payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.detach(task_id, ws)

    async def _send(self, ws, payload) -> None:
        if isinstance(payload, bytes):
            await ws.send_bytes(payload)
        else:
            await ws.send_text(payload)
