"""/ws/events 事件总线：客户端集合管理 + 广播（对齐 index.ts broadcast）。

消息格式：{"type": ..., "payload": ..., "ts": <ms>}。
broadcast 为同步函数（fire-and-forget），可在任意异步上下文调用。
"""
import asyncio
import json

from .config import now_ms


class EventSock:
    """一个 /ws/events 客户端连接，auth 消息后绑定 clientId。"""

    __slots__ = ("ws", "client_id")

    def __init__(self, ws):
        self.ws = ws
        self.client_id = None


class EventBus:
    def __init__(self):
        self._clients: set[EventSock] = set()

    def add(self, sock: EventSock) -> None:
        self._clients.add(sock)

    def remove(self, sock: EventSock) -> None:
        self._clients.discard(sock)

    def broadcast(self, type_: str, payload: dict) -> None:
        msg = json.dumps(
            {"type": type_, "payload": payload, "ts": now_ms()},
            ensure_ascii=False,
        )
        for sock in list(self._clients):
            try:
                asyncio.get_running_loop().create_task(self._send(sock, msg))
            except RuntimeError:
                # 不在事件循环内（不应发生），跳过
                pass

    async def _send(self, sock: EventSock, msg: str) -> None:
        try:
            await sock.ws.send_text(msg)
        except Exception:
            self._clients.discard(sock)
