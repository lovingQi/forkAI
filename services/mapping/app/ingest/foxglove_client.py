"""foxglove.websocket.v1 客户端。只订阅 ROS1 通道。"""
import asyncio
import json
import struct

import websockets

from ..errors import MappingError

SUBPROTOCOL = "foxglove.websocket.v1"
MAX_MESSAGE_BYTES = 2 * 1024 * 1024


class FoxgloveClient:
    def __init__(self, url: str):
        self.url = url
        self.channels: dict[int, dict] = {}
        self._ws = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._reader = None
        self._sub_ids: dict[int, int] = {}
        self._next_sub = 1
        self.closed_reason = None

    async def connect(self, timeout: float = 10) -> None:
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(self.url, subprotocols=[SUBPROTOCOL], max_size=MAX_MESSAGE_BYTES, proxy=None),
                timeout=timeout,
            )
        except TypeError:
            self._ws = await asyncio.wait_for(
                websockets.connect(self.url, subprotocols=[SUBPROTOCOL], max_size=MAX_MESSAGE_BYTES),
                timeout=timeout,
            )
        except Exception as exc:
            raise MappingError("MAPPING_INPUT_INVALID", f"连接 Foxglove Bridge 失败：{exc}") from exc
        self._reader = asyncio.create_task(self._read_loop())

    async def subscribe(self, channel_ids: list[int]) -> None:
        subs = []
        for channel_id in channel_ids:
            sub_id = self._next_sub
            self._next_sub += 1
            self._sub_ids[sub_id] = channel_id
            subs.append({"id": sub_id, "channelId": channel_id})
        await self._ws.send(json.dumps({"op": "subscribe", "subscriptions": subs}))

    async def next_message(self, timeout: float | None = None):
        if timeout is None:
            return await self._queue.get()
        try:
            return await asyncio.wait_for(self._queue.get(), timeout)
        except asyncio.TimeoutError:
            return None

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def _read_loop(self) -> None:
        try:
            async for message in self._ws:
                if isinstance(message, str):
                    self._on_text(message)
                else:
                    self._on_binary(message)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self.closed_reason = "bridge_disconnected"
            await self._queue.put({"kind": "closed", "reason": str(exc)})
            return
        self.closed_reason = "bridge_disconnected"
        await self._queue.put({"kind": "closed", "reason": "eof"})

    def _on_text(self, message: str) -> None:
        try:
            body = json.loads(message)
        except json.JSONDecodeError:
            return
        op = body.get("op")
        if op == "advertise":
            for channel in body.get("channels") or []:
                if channel.get("encoding") not in (None, "ros1"):
                    self.closed_reason = "bad_encoding"
                self.channels[int(channel["id"])] = channel
            self._queue.put_nowait({"kind": "advertise"})
        elif op == "unadvertise":
            self._queue.put_nowait({"kind": "unadvertise", "ids": body.get("channelIds") or []})
        elif op == "serverInfo":
            self._queue.put_nowait({"kind": "serverInfo"})

    def _on_binary(self, message: bytes) -> None:
        if len(message) < 13 or message[0] != 1:
            return
        if len(message) > MAX_MESSAGE_BYTES:
            return
        sub_id = struct.unpack_from("<I", message, 1)[0]
        timestamp = struct.unpack_from("<Q", message, 5)[0]
        channel_id = self._sub_ids.get(sub_id)
        if channel_id is None:
            return
        self._queue.put_nowait({
            "kind": "data",
            "channelId": channel_id,
            "timestamp": timestamp,
            "payload": message[13:],
        })
