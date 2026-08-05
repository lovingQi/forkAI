#!/usr/bin/env python3
"""协议符合性测试（A5）。

前置：mock-jarvis (:8080) 与 forkai-core (:19000) 已启动（新协议版）。
断言：
 (a) JarvisClient.start_route 打到 /api/control/scheduler 且 body 含 name + content 节点表
 (b) mock 的 current_routes.status 恒为 "running"（空闲与 route 执行中都成立）
 (c) focklift route 完整跑通：引擎判定 succeeded（复合判定：routes→TEMP_DEFAULT + 叉高达标）
 (d) mock_fail 路线：引擎判定 failed 并走 fail 边

用法（services/core 目录下）：.venv/bin/python scripts/protocol_conformance.py
"""
import asyncio
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

CORE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CORE_ROOT))

BASE = "http://127.0.0.1:19000"
MOCK = "http://127.0.0.1:8080"
RESULTS: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, cond))
    print(f"[PROTO] {'PASS' if cond else 'FAIL'}  {name}  {detail}", flush=True)


# ---- (a) 本地 stub 捕获 start_route 的 URL 与 body ----
_captured: dict = {}


class _Stub(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        _captured["path"] = self.path
        _captured["body"] = json.loads(body or b"{}")
        data = json.dumps({"succeed": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


async def test_a() -> None:
    from app.jarvis.client import JarvisClient

    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    client = JarvisClient({"jarvis": {"baseUrl": f"http://127.0.0.1:{port}"}})
    await client.start_route(
        {"name": "voice_focklift_t1", "content": {"a": {"cmd": "focklift", "pos": 150, "wait": 20, "tolerance": 20}}}
    )
    await client.close()
    srv.shutdown()
    path = _captured.get("path", "")
    body = _captured.get("body", {})
    ok = (
        path == "/api/control/scheduler"
        and body.get("name") == "voice_focklift_t1"
        and body.get("content", {}).get("a", {}).get("cmd") == "focklift"
    )
    check("a. start_route→/api/control/scheduler + name/content", ok,
          f"path={path} body_keys={sorted(body.keys())}")


async def main() -> int:
    await test_a()

    async with httpx.AsyncClient(timeout=30) as c:
        # 配对 + 解锁
        code = (await c.post(f"{BASE}/api/pair/start")).json()["code"]
        token = (await c.post(f"{BASE}/api/pair/confirm", json={"code": code})).json()["pairToken"]
        h = {"Authorization": f"Bearer {token}"}
        site = (await c.get(f"{BASE}/api/site", headers=h)).json()
        un = await c.post(f"{BASE}/api/site/unlock", headers=h, json={"code": site["code"]})
        if un.status_code == 409:
            await c.post(f"{BASE}/api/site/unlock", headers=h,
                         json={"code": site["code"], "force": True})

        # ---- (b) current_routes.status 恒 running ----
        st_idle = (await c.get(f"{BASE}/api/state", headers=h)).json()
        idle_ok = st_idle["current_routes"]["status"] == "running"
        # 起一条 focklift 路线，执行中再查一次
        fid = (await c.post(f"{BASE}/api/flows", headers=h, json={
            "name": "proto_c",
            "nodes": [{"id": "n1", "type": "focklift", "params": {"pos": 150}}],
            "edges": [],
        })).json()["id"]
        await c.post(f"{BASE}/api/flows/{fid}/start", headers=h)
        await asyncio.sleep(0.4)
        st_run = (await c.get(f"{BASE}/api/state", headers=h)).json()
        run_ok = st_run["current_routes"]["status"] == "running"
        check("b. current_routes.status 恒 running", idle_ok and run_ok,
              f"idle={st_idle['current_routes']!r} running={st_run['current_routes']!r}")

        # ---- (c) focklift 路线判定 succeeded ----
        deadline = time.monotonic() + 20
        st = {}
        while time.monotonic() < deadline:
            st = (await c.get(f"{BASE}/api/flow-engine/status", headers=h)).json()
            if st["flowStatus"] in ("succeeded", "failed"):
                break
            await asyncio.sleep(0.3)
        ok = st["flowStatus"] == "succeeded" and st["nodeStates"].get("n1") == "succeeded"
        check("c. focklift route 引擎判 succeeded", ok, f"final={st['flowStatus']} {st['nodeStates']}")

        # ---- (d) mock_fail → failed + fail 边 ----
        fid2 = (await c.post(f"{BASE}/api/flows", headers=h, json={
            "name": "proto_d",
            "nodes": [
                {"id": "n1", "type": "focklift", "params": {"pos": 210, "mock_fail": True}},
                {"id": "n2", "type": "charge", "params": {}},
            ],
            "edges": [{"from": "n1", "to": "n2", "on": "fail"}],
        })).json()["id"]
        await c.post(f"{BASE}/api/flows/{fid2}/start", headers=h)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            st = (await c.get(f"{BASE}/api/flow-engine/status", headers=h)).json()
            if st["flowStatus"] in ("succeeded", "failed"):
                break
            await asyncio.sleep(0.3)
        ok = (
            st["flowStatus"] == "succeeded"
            and st["nodeStates"].get("n1") == "failed"
            and st["nodeStates"].get("n2") == "succeeded"
        )
        check("d. mock_fail→failed+fail边", ok, f"final={st['flowStatus']} {st['nodeStates']}")

    fails = [n for n, ok in RESULTS if not ok]
    print(f"[PROTO] ===== 汇总: {len(RESULTS) - len(fails)}/{len(RESULTS)} PASS"
          + (f"，失败: {fails}" if fails else "，全部通过 ====="), flush=True)
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
