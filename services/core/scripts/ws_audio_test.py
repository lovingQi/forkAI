#!/usr/bin/env python3
"""WS /ws/audio 全链路联调（步骤16验证）。

前置：mock-jarvis (:8080) 与 forkai-core (:19000) 已启动。

流程：
1. HTTP 配对拿 pairToken，解锁现场锁
2. ptt 通道：连 /ws/audio，按 100ms 块发 piper 合成的 "前进" PCM → {"event":"end"}
   断言收到 partial/final，final.succeed=True 且 intent=MOVE_FWD
3. cabin 通道：先发 "玖物，玖物"（唤醒）再发 "前进"
   断言两次 final：第一次 WAKE，第二次 MOVE_FWD 且 succeed=True
"""
import asyncio
import base64
import io
import json
import sys
import wave
from pathlib import Path

import httpx
import numpy as np
import websockets

CORE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CORE_ROOT))

from app.config import load_config  # noqa: E402
from app.tts.piper import synthesize  # noqa: E402

BASE = "http://127.0.0.1:19000"
WS = "ws://127.0.0.1:19000/ws/audio"


def wav_b64_to_pcm16_16k(b64: str) -> bytes:
    raw = base64.b64decode(b64)
    with wave.open(io.BytesIO(raw), "rb") as w:
        rate = w.getframerate()
        samples = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    if rate != 16000:
        dur = len(samples) / rate
        n_out = int(dur * 16000)
        samples = np.interp(
            np.linspace(0, dur, n_out, endpoint=False),
            np.linspace(0, dur, len(samples), endpoint=False),
            samples,
        ).astype(np.int16)
    return samples.tobytes()


async def synth_pcm(cfg: dict, text: str) -> bytes:
    spoken = await synthesize(text, "ok", cfg)
    assert spoken.get("audio_base64"), f"piper 合成失败: {text}"
    return wav_b64_to_pcm16_16k(spoken["audio_base64"])


async def pair_and_unlock() -> str:
    async with httpx.AsyncClient(base_url=BASE, timeout=10) as c:
        code = (await c.post("/api/pair/start")).json()["code"]
        token = (await c.post("/api/pair/confirm", json={"code": code})).json()["pairToken"]
        h = {"Authorization": f"Bearer {token}"}
        site = (await c.get("/api/site", headers=h)).json()
        r = await c.post("/api/site/unlock", headers=h, json={"code": site["code"]})
        assert r.status_code == 200 and r.json().get("succeed"), f"unlock 失败: {r.text}"
        return token


async def feed_and_wait_final(ws, pcm: bytes, label: str) -> dict:
    block = 16000 * 2 // 10  # 100ms
    partials = 0
    for off in range(0, len(pcm), block):
        await ws.send(pcm[off : off + block])
        await asyncio.sleep(0.02)
        # 非阻塞抽干 partial
        try:
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.01))
                if msg.get("type") == "partial":
                    partials += 1
                elif msg.get("type") == "final":
                    print(f"[ws_test] {label}: 提前 endpoint final={msg.get('text')!r}")
                    return msg
        except asyncio.TimeoutError:
            pass
    await ws.send(json.dumps({"event": "end"}))
    deadline = asyncio.get_running_loop().time() + 15
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(1, deadline - asyncio.get_running_loop().time())))
        if msg.get("type") == "partial":
            partials += 1
            continue
        if msg.get("type") == "final":
            print(f"[ws_test] {label}: partials={partials} final_text={msg.get('text')!r}")
            return msg
        if msg.get("type") == "error":
            raise AssertionError(f"WS 错误: {msg}")


async def main() -> int:
    cfg = load_config()
    token = await pair_and_unlock()
    print("[ws_test] 配对+解锁 OK")

    ok = True

    # ---- ptt：前进 ----
    pcm = await synth_pcm(cfg, "前进")
    async with websockets.connect(WS) as ws:
        await ws.send(json.dumps({"pairToken": token, "channel": "ptt"}))
        final = await feed_and_wait_final(ws, pcm, "ptt/前进")
    hit = final.get("succeed") is True and final.get("intent", {}).get("name") == "MOVE_FWD"
    print(
        f"[ws_test] ptt/前进: succeed={final.get('succeed')} "
        f"intent={final.get('intent', {}).get('name')} utterance={final.get('utterance')!r} "
        f"{'PASS' if hit else 'FAIL'}"
    )
    ok = ok and hit

    # ---- cabin：玖物，玖物（唤醒）→ 前进 ----
    wake_pcm = await synth_pcm(cfg, "玖物，玖物")
    fwd_pcm = await synth_pcm(cfg, "前进")
    async with websockets.connect(WS) as ws:
        await ws.send(json.dumps({"pairToken": token, "channel": "cabin"}))
        f1 = await feed_and_wait_final(ws, wake_pcm, "cabin/唤醒")
        f2 = await feed_and_wait_final(ws, fwd_pcm, "cabin/前进")
    hit1 = f1.get("intent", {}).get("name") == "WAKE" and f1.get("succeed") is True
    hit2 = f2.get("succeed") is True and f2.get("intent", {}).get("name") == "MOVE_FWD"
    print(
        f"[ws_test] cabin/唤醒: succeed={f1.get('succeed')} intent={f1.get('intent', {}).get('name')} "
        f"utterance={f1.get('utterance')!r} {'PASS' if hit1 else 'FAIL'}"
    )
    print(
        f"[ws_test] cabin/前进: succeed={f2.get('succeed')} intent={f2.get('intent', {}).get('name')} "
        f"utterance={f2.get('utterance')!r} {'PASS' if hit2 else 'FAIL'}"
    )
    ok = ok and hit1 and hit2

    print("[ws_test] 总体:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
