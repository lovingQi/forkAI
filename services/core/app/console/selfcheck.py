"""开机自检。rosbridge 一期不参与通过判定。"""
import time


async def run_selfcheck(jarvis) -> dict:
    core = {"ok": True, "detail": "forkai-core"}
    started = time.perf_counter()
    try:
        await jarvis.get_state()
        jarvis_item = {"ok": True, "latencyMs": int((time.perf_counter() - started) * 1000)}
    except Exception as e:
        jarvis_item = {"ok": False, "error": str(e) or "jarvis unreachable"}
    rosbridge = {"ok": True, "status": "phase2", "detail": "二期接入"}
    return {
        "ok": bool(core["ok"] and jarvis_item["ok"]),
        "core": core,
        "jarvis": jarvis_item,
        "rosbridge": rosbridge,
    }
