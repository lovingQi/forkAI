#!/usr/bin/env python3
"""core 建图代理与门控。本进程内起一个假建图服务，不依赖已启动的 core。

用法（services/core 目录下）：.venv/bin/python scripts/mapping_proxy_test.py
"""
import asyncio
import sys
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, WebSocket

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))

RESULTS = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append(cond)
    print(f"[MAP-PROXY] {'PASS' if cond else 'FAIL'}  {name}  {detail}", flush=True)


def fake_mapping_app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/v1/mapping/status")
    async def status():
        return {"ok": True}

    @app.post("/api/v1/mapping/tasks")
    async def start():
        return {"id": "t1", "status": "starting"}

    @app.websocket("/api/v1/mapping/tasks/{task_id}/live")
    async def live(ws: WebSocket, task_id: str):
        await ws.accept()
        await ws.send_json({"type": "state", "taskId": task_id})
        await ws.receive()

    return app


async def main() -> int:
    server = uvicorn.Server(uvicorn.Config(fake_mapping_app(), host="127.0.0.1", port=19110, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    from app.main import app as core_app
    core_app.state.cfg.setdefault("mapping", {})["baseUrl"] = "http://127.0.0.1:19110"
    core_app.state.mapping_http = httpx.AsyncClient(timeout=5, trust_env=False)
    transport = httpx.ASGITransport(app=core_app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://core") as client:
            bare = await client.get("/api/mapping/status")
            check("未配对返回 401", bare.status_code == 401, str(bare.status_code))
            code = (await client.post("/api/pair/start")).json()["code"]
            token = (await client.post("/api/pair/confirm", json={"code": code})).json()["pairToken"]
            headers = {"Authorization": f"Bearer {token}"}
            got = await client.get("/api/mapping/status", headers=headers)
            check("已配对可以查看", got.status_code == 200 and got.json().get("ok") is True, got.text[:180])
            denied = await client.post("/api/mapping/tasks", headers=headers, json={})
            check("没有现场锁不能开始", denied.status_code == 403 and denied.json().get("error") == "no_site", denied.text[:180])
            site = (await client.get("/api/site", headers=headers)).json()
            await client.post("/api/site/unlock", headers=headers, json={"code": site["code"]})
            allowed = await client.post("/api/mapping/tasks", headers=headers, json={})
            check("持有现场锁可以开始", allowed.status_code == 200, allowed.text[:180])
        core_app.state.cfg["mapping"]["baseUrl"] = "http://127.0.0.1:59999"
        async with httpx.AsyncClient(transport=transport, base_url="http://core") as client:
            code = (await client.post("/api/pair/start")).json()["code"]
            token = (await client.post("/api/pair/confirm", json={"code": code})).json()["pairToken"]
            down = await client.get("/api/mapping/status", headers={"Authorization": f"Bearer {token}"})
            check(
                "建图服务连不上返回 502",
                down.status_code == 502 and down.json().get("error") == "mapping_unavailable",
                f"status={down.status_code} body={down.text[:180]}",
            )
        from starlette.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect
        core_app.state.cfg["mapping"]["baseUrl"] = "http://127.0.0.1:19110"
        closed = None
        try:
            with TestClient(core_app) as sync:
                with sync.websocket_connect("/ws/mapping/tasks/t1/live") as ws:
                    ws.send_json({"type": "auth", "pairToken": "nope"})
                    ws.receive_text()
        except WebSocketDisconnect as exc:
            closed = exc.code
        check("错误口令关闭实时通道", closed == 4401, str(closed))
    finally:
        await core_app.state.mapping_http.aclose()
        server.should_exit = True
        await task
    failed = RESULTS.count(False)
    print(f"[MAP-PROXY] {len(RESULTS) - failed}/{len(RESULTS)} passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
