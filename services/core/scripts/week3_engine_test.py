#!/usr/bin/env python3
"""Week 3 任务流引擎验证（步骤35-40）。

前置：mock-jarvis (:8080)、forkai-core (:19000)、llama-server (:19002，仅第7项回归需要)。
场景：
 1. 顺序流 focklift→follow_back→head 全部 succeeded
 2. 节点超时失败 + fail 边流转（focklift timeout_s=1 → charge）
 3. pause/resume（pause 停车挂起，resume 重发 route）
 4. cancel（剩余节点 skipped）
 5. 低电量自动 pause + charge 下发 + flow_charge_full 广播
 6. 分级锁定：流 running 时"前进"被拒，"停止"放行
 7. week2_regression.py 回归（会 pkill llama-server，放最后）

用法（services/core 目录下）：.venv/bin/python scripts/week3_engine_test.py
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import httpx
import websockets

CORE_ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:19000"
MOCK = "http://127.0.0.1:8080"
RESULTS: list = []
EVENTS: list = []  # /ws/events 收集


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, cond))
    print(f"[W3] {'PASS' if cond else 'FAIL'}  {name}  {detail}", flush=True)


async def ws_collector(token: str) -> None:
    async with websockets.connect(f"ws://127.0.0.1:19000/ws/events") as ws:
        await ws.send(json.dumps({"type": "auth", "pairToken": token}))
        async for raw in ws:
            try:
                EVENTS.append(json.loads(raw))
            except Exception:
                pass


async def wait_flow(c: httpx.AsyncClient, h: dict, want: str, timeout: float = 30.0) -> dict:
    """轮询引擎状态直到 flowStatus==want，返回最终 status。"""
    deadline = asyncio.get_running_loop().time() + timeout
    last = None
    while asyncio.get_running_loop().time() < deadline:
        last = (await c.get(f"{BASE}/api/flow-engine/status", headers=h)).json()
        if last["flowStatus"] == want:
            return last
        await asyncio.sleep(0.3)
    return last or {}


async def main() -> int:
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
        ws_task = asyncio.create_task(ws_collector(token))

        async def mkflow(name: str, nodes: list, edges: list, options: dict | None = None) -> str:
            body = {"name": name, "nodes": nodes, "edges": edges}
            if options:
                body["options"] = options
            r = await c.post(f"{BASE}/api/flows", headers=h, json=body)
            assert r.status_code == 200, f"建流失败: {r.text}"
            return r.json()["id"]

        # ---- 1. 顺序流 ----
        fid = await mkflow(
            "w3_seq",
            [
                {"id": "n1", "type": "focklift", "params": {"pos": 150}},
                {"id": "n2", "type": "follow_back", "params": {"start_name": "a点", "target_name": "b点"}},
                {"id": "n3", "type": "head", "params": {"angle": 90}},
            ],
            [
                {"from": "n1", "to": "n2", "on": "success"},
                {"from": "n2", "to": "n3", "on": "success"},
            ],
        )
        await c.post(f"{BASE}/api/flows/{fid}/start", headers=h)
        traj = []
        deadline = asyncio.get_running_loop().time() + 40
        while asyncio.get_running_loop().time() < deadline:
            st = (await c.get(f"{BASE}/api/flow-engine/status", headers=h)).json()
            snap = (st["flowStatus"], dict(st["nodeStates"]))
            if not traj or traj[-1] != snap:
                traj.append(snap)
                print(f"[W3] 轨迹: {snap}", flush=True)
            if st["flowStatus"] in ("succeeded", "failed", "cancelled"):
                break
            await asyncio.sleep(0.3)
        ok = (
            st["flowStatus"] == "succeeded"
            and all(st["nodeStates"][n] == "succeeded" for n in ("n1", "n2", "n3"))
        )
        check("1.顺序流三节点依次succeeded", ok, f"final={st['flowStatus']}")

        # ---- 2. 节点超时 + fail 边（focklift 75 需 ~1.5s，timeout_s=1 必超时） ----
        fid2 = await mkflow(
            "w3_failbranch",
            [
                {"id": "n1", "type": "focklift", "params": {"pos": 75, "timeout_s": 1}},
                {"id": "n2", "type": "charge", "params": {}},
            ],
            [{"from": "n1", "to": "n2", "on": "fail"}],
        )
        await c.post(f"{BASE}/api/flows/{fid2}/start", headers=h)
        st = await wait_flow(c, h, "succeeded", timeout=20)
        ok = (
            st["flowStatus"] == "succeeded"
            and st["nodeStates"].get("n1") == "failed"
            and st["nodeStates"].get("n2") == "succeeded"
        )
        check("2.超时失败+fail边流转", ok, f"nodeStates={st['nodeStates']}")

        # ---- 3. pause/resume ----
        fid3 = await mkflow(
            "w3_pause",
            [{"id": "n1", "type": "follow_back", "params": {"start_name": "a点", "target_name": "b点"}}],
            [],
        )
        await c.post(f"{BASE}/api/flows/{fid3}/start", headers=h)
        await asyncio.sleep(0.6)  # 节点 running（follow_back 模拟 2s）
        p = await c.post(f"{BASE}/api/flow-engine/pause", headers=h)
        await asyncio.sleep(0.3)
        st = (await c.get(f"{BASE}/api/flow-engine/status", headers=h)).json()
        paused_ok = p.json().get("succeed") and st["flowStatus"] == "paused"
        r = await c.post(f"{BASE}/api/flow-engine/resume", headers=h)
        resumed_ok = r.json().get("succeed")
        st = await wait_flow(c, h, "succeeded", timeout=20)
        ok = paused_ok and resumed_ok and st["flowStatus"] == "succeeded" \
            and st["nodeStates"].get("n1") == "succeeded"
        check("3.pause/resume", ok, f"paused_ok={paused_ok} resumed_ok={resumed_ok} final={st['flowStatus']}")

        # ---- 4. cancel ----
        fid4 = await mkflow(
            "w3_cancel",
            [
                {"id": "n1", "type": "follow_back", "params": {"start_name": "a点", "target_name": "b点"}},
                {"id": "n2", "type": "head", "params": {"angle": 90}},
            ],
            [{"from": "n1", "to": "n2", "on": "success"}],
        )
        await c.post(f"{BASE}/api/flows/{fid4}/start", headers=h)
        await asyncio.sleep(0.3)
        cc = await c.post(f"{BASE}/api/flow-engine/cancel", headers=h)
        await asyncio.sleep(0.5)
        st = (await c.get(f"{BASE}/api/flow-engine/status", headers=h)).json()
        ok = (
            cc.json().get("succeed")
            and st["flowStatus"] == "cancelled"
            and st["nodeStates"].get("n2") == "skipped"
        )
        check("4.cancel剩余skipped", ok, f"final={st['flowStatus']} nodeStates={st['nodeStates']}")

        # ---- 5. 低电量 ----
        fid5 = await mkflow(
            "w3_lowbat",
            [{"id": "n1", "type": "follow_back", "params": {"start_name": "c点", "target_name": "d点"}}],
            [],
        )
        await c.post(f"{BASE}/api/flows/{fid5}/start", headers=h)
        await asyncio.sleep(0.4)
        await c.post(f"{MOCK}/api/debug/state", json={"battery": 15})
        await asyncio.sleep(2.0)  # 引擎 poll 0.5s 内应检测并 pause + 下 charge
        st = (await c.get(f"{BASE}/api/flow-engine/status", headers=h)).json()
        low_paused = st["flowStatus"] == "paused"
        await asyncio.sleep(2.5)  # charge 模拟 2s 完成 → charing=true
        stt = (await c.get(f"{BASE}/api/state", headers=h)).json()
        charing = stt.get("charing") is True
        got_low_tts = any(
            e.get("type") == "tts" and "电量过低" in str(e.get("payload", {}).get("text", ""))
            for e in EVENTS
        )
        await c.post(f"{MOCK}/api/debug/state", json={"battery": 90})
        full_ok = False
        deadline = asyncio.get_running_loop().time() + 8
        while asyncio.get_running_loop().time() < deadline:
            if any(e.get("type") == "flow_charge_full" for e in EVENTS):
                full_ok = True
                break
            await asyncio.sleep(0.3)
        check("5.低电量自动pause+充电", low_paused and charing and got_low_tts and full_ok,
              f"paused={low_paused} charing={charing} tts={got_low_tts} charge_full={full_ok}")
        await c.post(f"{BASE}/api/flow-engine/cancel", headers=h)
        await asyncio.sleep(0.3)

        # ---- 6. 分级锁定 ----
        fid6 = await mkflow(
            "w3_lock",
            [{"id": "n1", "type": "follow_back", "params": {"start_name": "e点", "target_name": "f点"}}],
            [],
        )
        await c.post(f"{BASE}/api/flows/{fid6}/start", headers=h)
        await asyncio.sleep(0.3)
        r1 = await c.post(f"{BASE}/api/voice/text", headers=h,
                          json={"text": "前进", "channel": "ptt"})
        r2 = await c.post(f"{BASE}/api/voice/text", headers=h,
                          json={"text": "停止", "channel": "ptt"})
        d1, d2 = r1.json(), r2.json()
        ok = (
            d1.get("succeed") is False
            and d1.get("utterance") == "任务流执行中，无法下发运动指令"
            and d2.get("succeed") is True
        )
        check("6.分级锁定", ok, f"前进→{d1.get('utterance')!r} 停止succeed={d2.get('succeed')}")
        await wait_flow(c, h, "succeeded", timeout=20)

        ws_task.cancel()

    # ---- 7. Week2 回归（子进程；会 pkill llama-server，放最后） ----
    proc = subprocess.run(
        [str(CORE_ROOT / ".venv/bin/python"), str(CORE_ROOT / "scripts/week2_regression.py")],
        cwd=str(CORE_ROOT), capture_output=True, text=True, timeout=600,
    )
    tail = [ln for ln in proc.stdout.splitlines() if "汇总" in ln]
    ok = proc.returncode == 0
    check("7.week2回归", ok, tail[0] if tail else f"rc={proc.returncode}")

    fails = [n for n, ok in RESULTS if not ok]
    print(f"[W3] ===== 汇总: {len(RESULTS) - len(fails)}/{len(RESULTS)} PASS"
          + (f"，失败: {fails}" if fails else "，全部通过 ====="), flush=True)
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
