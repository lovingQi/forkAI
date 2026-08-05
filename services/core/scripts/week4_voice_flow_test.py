#!/usr/bin/env python3
"""Week 4 语音触发任务流 + 异常收尾验证（步骤47-49）。

前置：mock-jarvis (:8080)、forkai-core (:19000)、llama-server (:19002，仅回归项需要)。
脚本第6项会 pkill 并重启 mock-jarvis；第8项会 pkill 并重启 core（uvicorn）。

用法（services/core 目录下）：.venv/bin/python scripts/week4_voice_flow_test.py
"""
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
import websockets

CORE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = CORE_ROOT.parent.parent
BASE = "http://127.0.0.1:19000"
MOCK = "http://127.0.0.1:8080"
RESULTS: list = []
EVENTS: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, cond))
    print(f"[W4] {'PASS' if cond else 'FAIL'}  {name}  {detail}", flush=True)


async def pair(c: httpx.AsyncClient) -> str:
    code = (await c.post(f"{BASE}/api/pair/start")).json()["code"]
    return (await c.post(f"{BASE}/api/pair/confirm", json={"code": code})).json()["pairToken"]


async def say(c, token, text, channel="ptt"):
    r = await c.post(f"{BASE}/api/voice/text", headers={"Authorization": f"Bearer {token}"},
                     json={"text": text, "channel": channel}, timeout=30)
    return r.json()


async def wait_flow(c, h, want, timeout=30.0):
    deadline = time.monotonic() + timeout
    st = {}
    while time.monotonic() < deadline:
        st = (await c.get(f"{BASE}/api/flow-engine/status", headers=h)).json()
        if st["flowStatus"] == want:
            break
        await asyncio.sleep(0.3)
    return st


async def ws_collector(token: str) -> None:
    async with websockets.connect("ws://127.0.0.1:19000/ws/events") as ws:
        await ws.send(json.dumps({"type": "auth", "pairToken": token}))
        async for raw in ws:
            try:
                EVENTS.append(json.loads(raw))
            except Exception:
                pass


def wait_port(url: str, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            import urllib.request
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


async def main() -> int:
    global RESULTS
    async with httpx.AsyncClient(timeout=30) as c:
        token = await pair(c)
        h = {"Authorization": f"Bearer {token}"}
        ws_task = asyncio.create_task(ws_collector(token))

        # ---- 1. 预建两条中文流程 ----
        flows = (await c.get(f"{BASE}/api/flows", headers=h)).json()["flows"]
        names = {f["name"]: f["id"] for f in flows}
        if "取货演示流程" not in names:
            r = await c.post(f"{BASE}/api/flows", headers=h, json={
                "name": "取货演示流程",
                "nodes": [
                    {"id": "n1", "type": "focklift", "params": {"pos": 150}},
                    {"id": "n2", "type": "follow_back", "params": {"start_name": "a点", "target_name": "b点", "get_pallet": True}},
                    {"id": "n3", "type": "focklift", "params": {"pos": 75}},
                ],
                "edges": [{"from": "n1", "to": "n2", "on": "success"},
                          {"from": "n2", "to": "n3", "on": "success"}],
            })
            assert r.status_code == 200, r.text
        if "回充流程" not in names:
            r = await c.post(f"{BASE}/api/flows", headers=h, json={
                "name": "回充流程",
                "nodes": [{"id": "n1", "type": "charge", "params": {}}],
                "edges": [],
            })
            assert r.status_code == 200, r.text
        names = {f["name"]: f["id"] for f in (await c.get(f"{BASE}/api/flows", headers=h)).json()["flows"]}
        check("1.预建流程", "取货演示流程" in names and "回充流程" in names,
              f"flows={sorted(names)}")

        # ---- 2. 语音触发：执行取货演示流程 → 确认 → succeeded ----
        r = await say(c, token, "执行取货演示流程")
        ok = r.get("utterance") == "确认执行任务流取货演示流程吗"
        r2 = await say(c, token, "确认")
        ok = ok and r2.get("succeed") and r2.get("intent", {}).get("name") == "CONFIRM"
        st = await wait_flow(c, h, "succeeded", timeout=40)
        ok = ok and st["flowStatus"] == "succeeded"
        check("2.语音启动+确认+跑完", ok, f"confirm={r.get('utterance')!r} final={st['flowStatus']}")

        # ---- 3. 暂停/继续/取消 ----
        await c.post(f"{BASE}/api/flows/{names['取货演示流程']}/start", headers=h)
        await asyncio.sleep(0.5)
        r1 = await say(c, token, "暂停任务")
        st1 = (await c.get(f"{BASE}/api/flow-engine/status", headers=h)).json()
        r2 = await say(c, token, "继续任务")
        await asyncio.sleep(0.3)
        st2 = (await c.get(f"{BASE}/api/flow-engine/status", headers=h)).json()
        r3 = await say(c, token, "取消任务")
        st3 = (await c.get(f"{BASE}/api/flow-engine/status", headers=h)).json()
        ok = (st1["flowStatus"] == "paused" and st2["flowStatus"] == "running"
              and st3["flowStatus"] == "cancelled"
              and r1.get("succeed") and r2.get("succeed") and r3.get("succeed"))
        check("3.暂停/继续/取消", ok,
              f"{st1['flowStatus']}→{st2['flowStatus']}→{st3['flowStatus']}")

        # ---- 4. 模糊匹配 + 不存在 ----
        r = await say(c, token, "执行取货演示")
        ok = r.get("utterance", "").startswith("确认执行任务流取货演示流程")
        r = await say(c, token, "确认")
        st = await wait_flow(c, h, "succeeded", timeout=40)
        ok = ok and st["flowStatus"] == "succeeded"
        r = await say(c, token, "执行不存在的流程")
        ok = ok and r.get("succeed") is False and "未找到任务流" in r.get("utterance", "")
        check("4.模糊匹配+不存在", ok, f"模糊确认succeeded；不存在→{r.get('utterance')!r}")

        # ---- 5. 流控锁定兼容 ----
        await c.post(f"{BASE}/api/flows/{names['回充流程']}/start", headers=h)
        await asyncio.sleep(0.3)
        r1 = await say(c, token, "前进")
        r2 = await say(c, token, "暂停任务")
        ok = (r1.get("succeed") is False
              and r1.get("utterance") == "任务流执行中，无法下发运动指令"
              and r2.get("succeed") is True)
        check("5.分级锁定兼容", ok, f"前进→{r1.get('utterance')!r} 暂停任务succeed={r2.get('succeed')}")
        await c.post(f"{BASE}/api/flow-engine/cancel", headers=h)
        await asyncio.sleep(0.3)

        # ---- 6. 断网续跑 ----
        fid = names["取货演示流程"]
        await c.post(f"{BASE}/api/flows/{fid}/start", headers=h)
        await asyncio.sleep(0.5)
        subprocess.run(["pkill", "-f", "services/mock-jarvis"], check=False)
        await asyncio.sleep(3)
        mock_proc = subprocess.Popen(
            ["node", "services/mock-jarvis/src/index.js"], cwd=str(REPO_ROOT),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        wait_port(f"{MOCK}/api/state", timeout=15)
        st = await wait_flow(c, h, "succeeded", timeout=40)
        lost = any(e.get("type") == "flow_jarvis_lost" for e in EVENTS)
        back = any(e.get("type") == "flow_jarvis_back" for e in EVENTS)
        ok = st["flowStatus"] == "succeeded" and lost and back
        check("6.断网续跑", ok, f"final={st['flowStatus']} lost={lost} back={back}")

        # ---- 7. 急停退出现场 ----
        site = (await c.get(f"{BASE}/api/site", headers=h)).json()
        await c.post(f"{BASE}/api/site/unlock", headers=h, json={"code": site["code"], "force": True})
        before = len(EVENTS)
        await c.post(f"{MOCK}/api/debug/state", json={"alarm": "estop"})
        await asyncio.sleep(3.5)
        ev_site = [e for e in EVENTS[before:]
                   if e.get("type") == "site_changed" and e.get("payload", {}).get("holderClientId") is None]
        ev_tts = [e for e in EVENTS[before:]
                  if e.get("type") == "tts" and "急停" in str(e.get("payload", {}).get("text", ""))]
        r = await say(c, token, "前进")
        ok = (ev_site and ev_tts and r.get("succeed") is False
              and "未现场解锁" in r.get("utterance", ""))
        check("7.急停退出现场", bool(ok),
              f"site_changed={len(ev_site)} tts={len(ev_tts)} 前进→{r.get('utterance')!r}")
        await c.post(f"{MOCK}/api/debug/state", json={"alarm": "normal"})

        # ---- 8. 引擎快照恢复 ----
        await c.post(f"{BASE}/api/flows/{names['取货演示流程']}/start", headers=h)
        await asyncio.sleep(0.5)
        await c.post(f"{BASE}/api/flow-engine/pause", headers=h)
        await asyncio.sleep(0.5)
        snap_path = CORE_ROOT / "data" / "flows" / ".engine_state.json"
        snap_exists = snap_path.exists()
        subprocess.run(["pkill", "-f", "uvicorn app.main"], check=False)
        await asyncio.sleep(2)
        core_proc = subprocess.Popen(
            [str(CORE_ROOT / ".venv/bin/uvicorn"), "app.main:app", "--host", "127.0.0.1", "--port", "19000"],
            cwd=str(CORE_ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if not wait_port(f"{BASE}/api/health", timeout=25):
            check("8.引擎快照恢复", False, "core 重启失败")
            print("[W4] 后续项依赖 core，提前退出", flush=True)
            return 1
        token2 = await pair(c)
        h2 = {"Authorization": f"Bearer {token2}"}
        st = (await c.get(f"{BASE}/api/flow-engine/status", headers=h2)).json()
        restored = st["flowStatus"] == "paused" and st["flowId"] == names["取货演示流程"]
        await c.post(f"{BASE}/api/flow-engine/cancel", headers=h2)
        await asyncio.sleep(0.5)
        check("8.引擎快照恢复", snap_exists and restored and not snap_path.exists(),
              f"snapshot={snap_exists} restored={restored}({st['flowStatus']},{st['flowId']}) 清除={not snap_path.exists()}")

        # ---- 9. 码一次性/轮换 ----
        code1 = (await c.post(f"{BASE}/api/pair/start")).json()["code"]
        r1 = await c.post(f"{BASE}/api/pair/confirm", json={"code": code1})
        r2 = await c.post(f"{BASE}/api/pair/confirm", json={"code": code1})
        pair_once = r1.status_code == 200 and r2.status_code == 400 and r2.json().get("error") == "invalid_code"
        tk = r1.json()["pairToken"]
        h3 = {"Authorization": f"Bearer {tk}"}
        site1 = (await c.get(f"{BASE}/api/site", headers=h3)).json()
        un = await c.post(f"{BASE}/api/site/unlock", headers=h3, json={"code": site1["code"]})
        r3 = await c.post(f"{BASE}/api/site/unlock", headers=h3, json={"code": site1["code"]})
        site_once = un.status_code == 200 and r3.status_code == 400 and r3.json().get("error") == "invalid"
        check("9.配对码一次性+现场码轮换", pair_once and site_once,
              f"pair_once={pair_once} site_once={site_once}")

        ws_task.cancel()
        return 0 if _summary() else 1


def _summary() -> bool:
    fails = [n for n, ok in RESULTS if not ok]
    print(f"[W4] ===== 场景汇总: {len(RESULTS) - len(fails)}/{len(RESULTS)} PASS"
          + (f"，失败: {fails}" if fails else "，全部通过 ====="), flush=True)
    return not fails


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
