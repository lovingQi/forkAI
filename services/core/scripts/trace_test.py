#!/usr/bin/env python3
"""追踪模块检查。

离线：环形缓冲、任务生命周期、过期编号、任务流优先、总线映射、ContextVar。
在线：需 mock-jarvis(:8080) 与 forkai-core(:19000)。配对后发文本，并跑一条两节点任务流。

用法（services/core 目录下）：.venv/bin/python scripts/trace_test.py
"""
import asyncio
import sys
from pathlib import Path

import httpx

CORE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CORE_ROOT))

from app.events import EventBus  # noqa: E402
from app.tracer import Tracer  # noqa: E402

BASE = "http://127.0.0.1:19000"
RESULTS: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, cond))
    print(f"[TRACE] {'PASS' if cond else 'FAIL'}  {name}  {detail}")


class _Bus:
    def __init__(self):
        self.events = []
        self._listeners = []

    def add_listener(self, fn):
        self._listeners.append(fn)

    def broadcast(self, type_, payload):
        self.events.append((type_, payload))
        for fn in list(self._listeners):
            fn(type_, payload)


def test_offline() -> None:
    bus = _Bus()
    tracer = Tracer(bus, 3)
    for i in range(5):
        tracer.emit("OBS", str(i))
    hist = tracer.history(10)
    check("容量只留最近3条", [e["msg"] for e in hist] == ["2", "3", "4"], str([e["msg"] for e in hist]))

    bus2 = _Bus()
    tr = Tracer(bus2, 50)
    tr.mission_begin("voice-1", "voice", "前进", [{"id": "s0", "status": "queued", "call": "drive.forward()", "label": "前进", "inserted": False, "note": None}])
    tr.mission_step("voice-1", "s0", "running")
    tr.mission_step("voice-1", "s0", "sent")
    tr.mission_status("voice-1", "succeeded")
    mission = tr.current_mission()
    check(
        "任务生命周期到终态",
        mission["status"] == "succeeded" and mission["steps"][0]["status"] == "sent" and mission["endedTs"],
        str(mission["status"]),
    )
    before = mission["steps"][0]["status"]
    tr.mission_step("stale", "s0", "failed")
    tr.mission_status("stale", "failed")
    check("过期编号被忽略", tr.current_mission()["steps"][0]["status"] == before and tr.current_mission()["status"] == "succeeded")

    tr.mission_begin("flow-1", "flow", "取货", [{"id": "n1", "status": "running", "call": "fork.lift(pos=150)", "label": "货叉", "inserted": False, "note": None}])
    tr.mission_status("flow-1", "running")
    tr.mission_begin("voice-2", "voice", "不要覆盖", [{"id": "s0", "status": "queued"}])
    check("任务流优先不被语音覆盖", tr.current_mission()["traceId"] == "flow-1" and tr.current_mission()["source"] == "flow")

    tr.mission_insert("flow-1", "n1", {"id": "auto-charge", "call": "task.charge(goal=auto)", "label": "低电量自动充电", "status": "running"})
    tr.mission_sync("flow-1", {"n1": "paused"}, "paused")
    steps = {s["id"]: s for s in tr.current_mission()["steps"]}
    check(
        "插入步骤在同步后仍在",
        steps["n1"]["status"] == "paused" and steps["auto-charge"]["inserted"] is True and steps["auto-charge"]["status"] == "running",
    )

    eb = EventBus()
    tr3 = Tracer(eb, 80)
    eb.add_listener(tr3.on_bus_event)
    eb.broadcast("tts", {"text": "好的，前进", "ttsEngine": "piper"})
    eb.broadcast("watchdog_stop", {})
    eb.broadcast("site_changed", {"holderClientId": "c1"})
    eb.broadcast("site_changed", {"holderClientId": None, "reason": "estop"})
    eb.broadcast("wake_armed", {"clientId": "c1"})
    eb.broadcast("flow_jarvis_lost", {"failures": 1})
    eb.broadcast("flow_jarvis_lost", {"failures": 2})
    eb.broadcast("flow_jarvis_back", {})
    eb.broadcast("flow_charge_full", {"battery": 80})
    eb.broadcast("trace", {"cat": "OBS"})
    cats = [(e["cat"], e["msg"], (e.get("data") or {}).get("metric", {}).get("where")) for e in tr3.history(80)]
    check("SAY 本地 piper", any(c == "SAY" and w == "local" and "前进" in m for c, m, w in cats))
    check("看门狗 GUARD", any(c == "GUARD" and "看门狗" in m for c, m, w in cats))
    check("急停 GUARD", any("急停触发" in m for c, m, w in cats))
    check("唤醒 HEAR", any(c == "HEAR" and "唤醒窗口" in m for c, m, w in cats))
    check("失联只记一次", sum(1 for c, m, w in cats if "失联" in m) == 1)
    check("恢复 LINK", any("车端恢复" in m for c, m, w in cats))
    check("充满 GUARD", any("等待人工继续" in m for c, m, w in cats))
    check("trace 事件不回环", sum(1 for c, m, w in cats if c == "SAY") == 1)


async def _ctx_child(tracer: Tracer) -> None:
    tracer.emit("OBS", "child")


def test_context() -> None:
    async def main():
        bus = _Bus()
        tracer = Tracer(bus, 20)
        tid = tracer.begin("utterance", "ctx")
        await asyncio.create_task(_ctx_child(tracer))
        msgs = [e for e in tracer.history(20) if e["msg"] == "child"]
        check("ContextVar 传到子任务", bool(msgs) and msgs[0]["traceId"] == tid, str(msgs[:1]))

    asyncio.run(main())


async def online() -> None:
    async with httpx.AsyncClient(timeout=30, trust_env=False) as c:
        health = await c.get(f"{BASE}/api/health")
        check("core 在线", health.status_code == 200, health.text[:80])
        code = (await c.post(f"{BASE}/api/pair/start")).json()["code"]
        conf = (await c.post(f"{BASE}/api/pair/confirm", json={"code": code})).json()
        token = conf["pairToken"]
        h = {"Authorization": f"Bearer {token}"}
        said = await c.post(f"{BASE}/api/voice/text", headers=h, json={"text": "前进", "channel": "ptt"})
        check("文本指令受理", said.status_code == 200, said.text[:120])
        snap = (await c.get(f"{BASE}/api/trace?limit=300", headers=h)).json()
        entries = snap.get("entries") or []
        check("历史含 HEAR", any(e.get("cat") == "HEAR" and "前进" in str(e.get("msg")) for e in entries))
        check("历史含 NLU", any(e.get("cat") == "NLU" for e in entries))

        await c.post(f"{BASE}/api/flow-engine/cancel", headers=h)
        flow = {
            "name": "trace在线检查",
            "nodes": [
                {"id": "n1", "type": "focklift", "params": {"pos": 150}},
                {"id": "n2", "type": "focklift", "params": {"pos": 75}},
            ],
            "edges": [{"from": "n1", "to": "n2", "on": "success"}],
        }
        created = await c.post(f"{BASE}/api/flows", headers=h, json=flow)
        check("创建两节点流程", created.status_code == 200 and created.json().get("id"), created.text[:160])
        fid = created.json().get("id")
        started = await c.post(f"{BASE}/api/flows/{fid}/start", headers=h)
        check("启动任务流", started.status_code == 200, started.text[:120])
        mission = None
        entries = []
        for _ in range(40):
            snap = (await c.get(f"{BASE}/api/trace?limit=300", headers=h)).json()
            mission = snap.get("mission")
            entries = snap.get("entries") or []
            if mission and mission.get("source") == "flow" and mission.get("status") in ("succeeded", "failed", "cancelled"):
                break
            await asyncio.sleep(0.5)
        check(
            "任务来源 flow 且终态成功",
            bool(mission) and mission.get("source") == "flow" and mission.get("status") == "succeeded",
            str((mission or {}).get("status")),
        )
        statuses = {s.get("id"): s.get("status") for s in (mission or {}).get("steps") or []}
        check("节点终态 done", statuses.get("n1") == "done" and statuses.get("n2") == "done", str(statuses))
        flow_entries = [e for e in entries if mission and e.get("traceId") == mission.get("traceId")]
        check("有 ACT", any(e.get("cat") == "ACT" for e in flow_entries))
        check("有 TASK", any(e.get("cat") == "TASK" for e in flow_entries))


def main() -> int:
    test_offline()
    test_context()
    try:
        asyncio.run(online())
    except Exception as e:
        check("在线部分", False, str(e))
    failed = [n for n, ok in RESULTS if not ok]
    print(f"[TRACE] {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
