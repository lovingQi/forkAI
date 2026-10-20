"""WS 路由（对齐 index.ts upgrade 处理）。

- /ws/events：事件通道；客户端可发 {"type":"auth","pairToken":...} 绑定 clientId。
- /ws/high、/ws/low：连 jarvis ws://.../ws/high|low 双向管道——
  仅把下游数据转发给客户端（客户端上行消息不转发），任一侧关闭则两侧都关。
"""
import asyncio
import json

import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..events import EventSock

router = APIRouter()


def _jarvis_ws_url(cfg: dict, kind: str) -> str:
    base = cfg["jarvis"]["baseUrl"]
    if base.startswith("http"):
        base = "ws" + base[4:]
    return f"{base.rstrip('/')}/ws/{kind}"


def _loopback(url: str) -> bool:
    u = (url or "").lower()
    return "127.0.0.1" in u or "localhost" in u or "[::1]" in u


@router.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    await ws.accept()
    sock = EventSock(ws)
    ws.app.state.bus.add(sock)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if isinstance(msg, dict) and msg.get("type") == "auth" and msg.get("pairToken"):
                rec = ws.app.state.sessions.resolve_token(str(msg["pairToken"]))
                if rec:
                    sock.client_id = rec["clientId"]
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        ws.app.state.bus.remove(sock)


async def _upstream_to_client(client: WebSocket, upstream) -> None:
    async for msg in upstream:
        if isinstance(msg, bytes):
            await client.send_bytes(msg)
        else:
            await client.send_text(msg)


async def _client_reader(client: WebSocket) -> None:
    # TS 版不转发客户端上行数据，仅监听关闭
    while True:
        await client.receive()


async def _pipe_jarvis(client: WebSocket, kind: str) -> None:
    await client.accept()
    url = _jarvis_ws_url(client.app.state.cfg, kind)
    try:
        kwargs = {}
        if _loopback(url):
            kwargs["proxy"] = None
        try:
            upstream = await websockets.connect(url, **kwargs)
        except TypeError:
            upstream = await websockets.connect(url)
    except Exception:
        await client.close()
        return
    try:
        tasks = [
            asyncio.ensure_future(_upstream_to_client(client, upstream)),
            asyncio.ensure_future(_client_reader(client)),
        ]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
    except Exception:
        pass
    finally:
        try:
            await upstream.close()
        except Exception:
            pass
        try:
            await client.close()
        except Exception:
            pass


@router.websocket("/ws/high")
async def ws_high(ws: WebSocket):
    await _pipe_jarvis(ws, "high")


@router.websocket("/ws/low")
async def ws_low(ws: WebSocket):
    await _pipe_jarvis(ws, "low")


@router.websocket("/ws/mapping/tasks/{task_id}/live")
async def ws_mapping_live(ws: WebSocket, task_id: str):
    await ws.accept()
    try:
        raw = await asyncio.wait_for(ws.receive_text(), 5)
        msg = json.loads(raw)
        rec = ws.app.state.sessions.resolve_token(str((msg or {}).get("pairToken") or "")) if isinstance(msg, dict) else None
        if not rec:
            await ws.close(code=4401)
            return
    except Exception:
        await ws.close(code=4401)
        return
    base = str((ws.app.state.cfg.get("mapping") or {}).get("baseUrl") or "")
    if base.startswith("https://"):
        upstream_url = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        upstream_url = "ws://" + base[len("http://"):]
    else:
        await ws.close()
        return
    upstream_url = upstream_url.rstrip("/") + f"/api/v1/mapping/tasks/{task_id}/live"
    try:
        kwargs = {"proxy": None} if _loopback(upstream_url) else {}
        try:
            upstream = await websockets.connect(upstream_url, **kwargs)
        except TypeError:
            upstream = await websockets.connect(upstream_url)
    except Exception:
        await ws.close()
        return

    async def upstream_to_client():
        async for msg in upstream:
            if isinstance(msg, bytes):
                await ws.send_bytes(msg)
            else:
                await ws.send_text(msg)

    async def discard_client():
        while True:
            await ws.receive()

    try:
        tasks = [asyncio.ensure_future(upstream_to_client()), asyncio.ensure_future(discard_client())]
        _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
    except Exception:
        pass
    finally:
        try:
            await upstream.close()
        except Exception:
            pass
        try:
            await ws.close()
        except Exception:
            pass
