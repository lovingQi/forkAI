"""启动预热：补齐固定话术与可枚举参数的云端缓存。"""
from __future__ import annotations

import asyncio

from ..executor import _ALARM_EXPLAIN, _ALARM_MAP, _MODE_MAP
from ..speak import render
from ..tasks.schemas import TASK_SCHEMAS
from .cache import cache_get, cache_key, cache_put
from .cloud import cloud_enabled, synthesize_cloud
from .service import _cloud_ids


def build_prewarm_texts(cfg: dict, flow_names: list[str]) -> list[str]:
    texts: list[str] = []
    static_keys = [
        "wake_ack",
        "move_fwd",
        "move_back",
        "turn_left",
        "turn_right",
        "stop",
        "speed_up",
        "speed_down",
        "idle",
        "dock",
        "fork_up",
        "fork_down",
        "cancel_ok",
        "task_get_pallet",
        "timeout_cancel",
        "query_task_none",
        "fail_unpaired",
        "fail_no_site",
        "fail_lock",
        "fail_unknown",
        "fail_goto",
        "fail_flow_running",
        "flow_low_battery",
        "flow_paused",
        "flow_resumed",
        "flow_cancelled",
        "flow_not_running",
        "alarm_estop_exit",
    ]
    for key in static_keys:
        texts.append(render(key))
    for schema in TASK_SCHEMAS.values():
        for spec in schema.get("params", {}).values():
            ask = spec.get("ask")
            if ask:
                texts.append(ask)
    texts.extend(_ALARM_EXPLAIN.values())
    for mode in _MODE_MAP.values():
        texts.append(render("query_mode", {"mode": mode}))
    for alarm in _ALARM_MAP.values():
        texts.append(render("query_alarm", {"alarm": alarm}))
    for state in ("已使能", "已断开"):
        texts.append(render("query_motor", {"state": state}))
    for n in range(0, 101):
        texts.append(render("query_battery", {"n": n}))
        texts.append(render("query_speed", {"n": n}))
    for n in range(75, 211):
        texts.append(render("query_fork", {"n": n}))
        texts.append(render("fork_to", {"n": n}))
    for n in range(5, 41):
        texts.append(render("speed_set", {"n": n}))
    for mag in (30, 45, 60, 90, 120, 135, 180, 270, 360):
        texts.append(render("task_head", {"n": mag}))
        texts.append(render("task_head", {"n": -mag}))
    for name in flow_names:
        if name:
            texts.append(render("flow_started", {"name": name}))
            texts.append(render("confirm_flow", {"name": name}))
    seen: set[str] = set()
    out: list[str] = []
    for t in texts:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


async def prewarm(cfg: dict, flow_store, stop_event: asyncio.Event) -> dict:
    """跳过已缓存键，并发 3，连续失败 3 次终止。"""
    stats = {"total": 0, "hit": 0, "synthesized": 0, "failed": 0}
    if not cloud_enabled(cfg):
        print("[forkai-core] TTS 预热跳过：云端未启用")
        return stats
    flow_names: list[str] = []
    if flow_store is not None:
        flow_names = [f.get("name") or "" for f in await flow_store.list()]
    texts = build_prewarm_texts(cfg, flow_names)
    stats["total"] = len(texts)
    model, voice = _cloud_ids(cfg)
    sem = asyncio.Semaphore(3)
    consecutive_fail = 0
    lock = asyncio.Lock()

    async def one(text: str) -> None:
        nonlocal consecutive_fail
        if stop_event.is_set():
            return
        key = cache_key(model, voice, text)
        if cache_get(cfg, key) is not None:
            async with lock:
                stats["hit"] += 1
            return
        async with sem:
            if stop_event.is_set():
                return
            try:
                wav = await synthesize_cloud(cfg, text)
                cache_put(cfg, key, wav)
                async with lock:
                    consecutive_fail = 0
                    stats["synthesized"] += 1
            except Exception as e:
                async with lock:
                    consecutive_fail += 1
                    stats["failed"] += 1
                    fail_n = consecutive_fail
                print(f"[forkai-core] TTS 预热失败: {e}")
                if fail_n >= 3:
                    stop_event.set()

    await asyncio.gather(*(one(t) for t in texts))
    print(
        f"[forkai-core] TTS 预热完成 total={stats['total']} hit={stats['hit']} "
        f"synthesized={stats['synthesized']} failed={stats['failed']}"
    )
    return stats
