#!/usr/bin/env python3
"""2D 在线建图的 Mock 流程。前置：本目录 .venv 已安装依赖。Bridge 固定 8765。

用法（services/mapping 目录下）：.venv/bin/python scripts/mapping_flow_test.py
"""
import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
import zipfile
from io import BytesIO
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "bin" / "python"
RESULTS = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append(cond)
    print(f"[MAP-FLOW] {'PASS' if cond else 'FAIL'}  {name}  {detail}", flush=True)


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), 0.2):
            return True
    except OSError:
        return False


class Stack:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.engine_port = free_port()
        self.api_port = free_port()
        self.procs: list[subprocess.Popen] = []
        self.cfg = tmp / "mapping.config.yaml"

    def write_config(self) -> None:
        lua_dir = self.tmp / "lua"
        lua_dir.mkdir(parents=True, exist_ok=True)
        (lua_dir / "range_odom_2d_server.lua").write_text('include "map_builder_server.lua"\n', encoding="utf-8")
        data = {
            "server": {"host": "127.0.0.1", "port": self.api_port},
            "dataDir": str(self.tmp / "data"),
            "minFreeBytes": 1,
            "foxglove": {"url": "ws://127.0.0.1:8765", "preflightTimeoutMs": 8000, "startTimeoutMs": 8000},
            "robot": {"urdf": str(ROOT / "config" / "robot.urdf"), "resourceId": "fork-01"},
            "engines": {"cartographer": {
                "enabled": True,
                "bin": str(ROOT / "devtools" / "fake_cartographer.sh"),
                "protoRoot": str(ROOT / "devtools" / "proto"),
                "configDir": str(lua_dir),
                "configBasename": "range_odom_2d_server.lua",
                "profileRoot": str(ROOT / "devtools" / "sample-profiles"),
                "endpoint": f"127.0.0.1:{self.engine_port}",
                "engineVersion": "test",
                "buildId": "test",
                "readyTimeoutMs": 10000,
                "stopTimeoutMs": 3000,
                "killTimeoutMs": 3000,
            }},
        }
        self.cfg.write_text(yaml.safe_dump(data), encoding="utf-8")

    def start(self) -> None:
        env = os.environ.copy()
        env["FORKAI_MAPPING_CONFIG"] = str(self.cfg)
        self.procs.append(subprocess.Popen(
            [str(PY), str(ROOT / "devtools" / "fake_bridge.py"), "--port", "8765"],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ))
        self.procs.append(subprocess.Popen(
            [str(PY), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(self.api_port)],
            cwd=str(ROOT), env=env, stdout=open(self.tmp / "api.log", "ab"), stderr=subprocess.STDOUT,
        ))
        deadline = time.time() + 20
        while time.time() < deadline:
            if port_open(self.api_port) and port_open(8765):
                return
            if self.procs[-1].poll() is not None:
                raise RuntimeError((self.tmp / "api.log").read_text(errors="replace")[-2000:])
            time.sleep(0.2)
        raise RuntimeError("建图服务或 Bridge 没有起来")

    def stop(self) -> None:
        for proc in self.procs:
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
        for proc in self.procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        kill_matching("fake_engine.py")

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.api_port}/api/v1/mapping"


def kill_matching(text: str) -> None:
    try:
        out = subprocess.check_output(["pgrep", "-f", text], text=True)
    except subprocess.CalledProcessError:
        return
    for pid in out.split():
        try:
            os.kill(int(pid), signal.SIGKILL)
        except OSError:
            pass


def draft() -> dict:
    return {
        "dimension": "2d", "mode": "online", "name": "流程测试", "profileId": "dual-range-odom-2d",
        "inputs": {
            "ranges": [
                {"sensorId": "front-laser", "topic": "/front/scan"},
                {"sensorId": "back-laser", "topic": "/back/scan"},
            ],
            "odometry": {"topic": "/jarvis/odom"},
        },
    }


async def wait_task(client: httpx.AsyncClient, base: str, task_id: str, statuses: set[str], timeout: float = 20) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = (await client.get(f"{base}/tasks/{task_id}")).json()
        if last.get("status") in statuses:
            return last
        await asyncio.sleep(0.3)
    return last


async def control(cmd: dict) -> None:
    import websockets
    async with websockets.connect("ws://127.0.0.1:8765/control") as ws:
        await ws.send(json.dumps(cmd))


async def scenario_happy(stack: Stack) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        status = (await client.get(f"{stack.base}/status")).json()
        check("引擎文件可用", status["capabilities"]["2d"]["online"] is True, str(status["engines"]))
        ready = await client.post(f"{stack.base}/drafts/validate", json=draft())
        check("预检通过", ready.status_code == 200 and ready.json().get("status") == "ready", ready.text[:300])
        created = await client.post(f"{stack.base}/tasks", json=draft())
        check("创建任务", created.status_code == 201, created.text[:300])
        task_id = created.json()["id"]
        running = await wait_task(client, stack.base, task_id, {"running", "failed"})
        check("任务进入建图", running.get("status") == "running", str(running.get("status")) + str(running.get("error")))
        if running.get("status") != "running":
            return
        await asyncio.sleep(1.2)
        live = await client.get(f"{stack.base}/tasks/{task_id}/activity")
        check("活动事件有序号", live.status_code == 200 and live.json()["latestSequence"] >= 1)
        finished = await client.post(f"{stack.base}/tasks/{task_id}/finish")
        check("完成返回 202", finished.status_code == 202, finished.text[:200])
        done = await wait_task(client, stack.base, task_id, {"completed", "failed"})
        check("保存完成", done.get("status") == "completed" and bool(done.get("projectId")), str(done.get("error")))
        if done.get("status") != "completed":
            return
        again = await client.post(f"{stack.base}/tasks/{task_id}/finish")
        check("重复完成被拒绝", again.status_code == 409, str(again.status_code))
        exported = await client.get(f"{stack.base}/maps/{done['projectId']}/export.zip")
        names = zipfile.ZipFile(BytesIO(exported.content)).namelist()
        check("ZIP 只有工程清单和两份产物", set(names) == {"project.json", "map.json", "mapping_state.mcap"}, str(names))


async def scenario_cancel(stack: Stack) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        created = await client.post(f"{stack.base}/tasks", json={**draft(), "name": "取消"})
        task_id = created.json()["id"]
        await wait_task(client, stack.base, task_id, {"running", "failed"})
        response = await client.post(f"{stack.base}/tasks/{task_id}/cancel")
        done = await wait_task(client, stack.base, task_id, {"cancelled", "failed", "completed"})
        check("取消不保留地图", response.status_code == 200 and done.get("status") == "cancelled" and not done.get("projectId"), str(done.get("status")))


async def scenario_faults(stack: Stack) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        await control({"cmd": "drop", "topic": "/back/scan"})
        ready = await client.post(f"{stack.base}/drafts/validate", json=draft())
        check("缺一路激光时预检失败", ready.status_code == 200 and ready.json().get("status") == "error", ready.text[:240])
        await control({"cmd": "close"})
        await asyncio.sleep(0.5)
        # 重新拉起 Bridge，供后续用例使用。由外层负责进程仍在；close 只关连接，drop 仍在。
        # 新连接仍会丢 /back/scan。这里不再恢复 drop，后续用例各自新开栈。


async def scenario_disconnect_and_save(stack: Stack) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        created = await client.post(f"{stack.base}/tasks", json={**draft(), "name": "中断保存"})
        task_id = created.json()["id"]
        running = await wait_task(client, stack.base, task_id, {"running", "failed"})
        if running.get("status") != "running":
            check("中断前已在建图", False, str(running.get("error")))
            return
        await control({"cmd": "close"})
        paused = await wait_task(client, stack.base, task_id, {"ready-to-save", "failed"})
        check("断线后待保存", paused.get("status") == "ready-to-save", str(paused.get("status")) + str(paused.get("stopReason")))
        if paused.get("status") != "ready-to-save":
            return
        # Bridge 还在，完成时不再需要传感器。
        await client.post(f"{stack.base}/tasks/{task_id}/finish")
        done = await wait_task(client, stack.base, task_id, {"completed", "failed"})
        check("中断后仍可保存", done.get("status") == "completed", str(done.get("error")))


async def scenario_rewind(stack: Stack) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        created = await client.post(f"{stack.base}/tasks", json={**draft(), "name": "回退"})
        task_id = created.json()["id"]
        await wait_task(client, stack.base, task_id, {"running", "failed"})
        await control({"cmd": "rewind"})
        done = await wait_task(client, stack.base, task_id, {"failed", "ready-to-save", "cancelled"})
        check("时间回退会结束输入", done.get("status") in {"failed", "ready-to-save"}, str(done.get("status")))


async def scenario_kill_engine(stack: Stack) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        before = (await client.get(f"{stack.base}/maps")).json()["maps"]
        created = await client.post(f"{stack.base}/tasks", json={**draft(), "name": "杀引擎"})
        task_id = created.json()["id"]
        running = await wait_task(client, stack.base, task_id, {"running", "failed"})
        if running.get("status") != "running":
            check("杀引擎前已在建图", False, str(running.get("error")))
            return
        kill_matching("fake_engine.py")
        done = await wait_task(client, stack.base, task_id, {"failed", "interrupted"})
        after = (await client.get(f"{stack.base}/maps")).json()["maps"]
        check("引擎退出后失败且不发布地图", done.get("status") == "failed" and len(after) == len(before), str(done.get("status")))


async def scenario_race(stack: Stack) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        created = await client.post(f"{stack.base}/tasks", json={**draft(), "name": "竞争"})
        task_id = created.json()["id"]
        await wait_task(client, stack.base, task_id, {"running", "failed"})
        await asyncio.gather(
            client.post(f"{stack.base}/tasks/{task_id}/finish"),
            client.post(f"{stack.base}/tasks/{task_id}/cancel"),
        )
        done = await wait_task(client, stack.base, task_id, {"completed", "cancelled", "failed"})
        check("完成和取消竞争后任务结束", done.get("status") in {"completed", "cancelled", "failed"}, str(done.get("status")))


async def scenario_restart(stack: Stack) -> None:
    task_id = "restart-me"
    folder = stack.tmp / "data" / "tasks" / task_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "task.json").write_text(json.dumps({
        "id": task_id, "dimension": "2d", "mode": "online", "name": "残留", "profileId": "dual-range-odom-2d",
        "profileRevision": "1-odom", "inputs": draft()["inputs"], "snapshot": {}, "status": "running",
        "stopReason": None, "stats": {}, "error": None, "projectId": None, "createdAt": "", "updatedAt": "",
    }), encoding="utf-8")
    stack.stop()
    stack.procs.clear()
    stack.start()
    async with httpx.AsyncClient(timeout=20) as client:
        body = (await client.get(f"{stack.base}/tasks/{task_id}")).json()
        check("重启后残留任务变为中断", body.get("status") == "interrupted" and body.get("stopReason") == "service_restarted", str(body.get("status")))


async def main() -> int:
    if port_open(8765):
        print("[MAP-FLOW] FAIL  8765 已被占用，跳过流程测试", flush=True)
        return 1
    tmp = Path("/tmp/forkai-mapping-flow")
    if tmp.exists():
        import shutil
        shutil.rmtree(tmp)
    tmp.mkdir()
    stack = Stack(tmp)
    stack.write_config()
    try:
        stack.start()
        await scenario_happy(stack)
        await scenario_cancel(stack)
        await scenario_faults(stack)
    finally:
        stack.stop()
    # 故障用例改过 Bridge 状态，后面的用例各用新进程。
    for scenario in (scenario_disconnect_and_save, scenario_rewind, scenario_kill_engine, scenario_race):
        stack = Stack(tmp)
        stack.write_config()
        try:
            stack.start()
            await scenario(stack)
        finally:
            stack.stop()
    stack = Stack(tmp)
    stack.write_config()
    try:
        stack.start()
        await scenario_restart(stack)
    finally:
        stack.stop()
    failed = RESULTS.count(False)
    print(f"[MAP-FLOW] {len(RESULTS) - failed}/{len(RESULTS)} passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
