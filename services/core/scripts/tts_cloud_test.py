#!/usr/bin/env python3
"""TTS 云端缓存链路断言。

前置：网络可达；环境变量 FORKAI_TTS_API_KEY 已设置；piper 模型可用（第 3 项兜底）。

用法（services/core 目录下）：
    .venv/bin/python scripts/tts_cloud_test.py
"""
import asyncio
import base64
import copy
import io
import sys
import tempfile
import time
import wave
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CORE_ROOT))

from app.config import load_config  # noqa: E402
from app.tts.cloud import normalize_wav  # noqa: E402
from app.tts.service import synthesize  # noqa: E402

RESULTS: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, cond))
    print(f"[tts_cloud] {'PASS' if cond else 'FAIL'}  {name}  {detail}")


def wav_meta(raw: bytes):
    with wave.open(io.BytesIO(raw), "rb") as w:
        nframes = w.getnframes()
        rate = w.getframerate()
        pcm = w.readframes(nframes)
        return rate, nframes, len(pcm)


async def main() -> int:
    base = load_config()
    cfg = copy.deepcopy(base)
    tts = dict(cfg.get("tts") or {})
    cloud = dict(tts.get("cloud") or {})
    tmp = tempfile.mkdtemp(prefix="forkai_tts_test_")
    tts["cache_dir"] = tmp
    tts["cloud"] = cloud
    cfg["tts"] = tts

    text = "好的，前进"
    spoken = await synthesize(text, "ok", cfg)
    raw = base64.b64decode(spoken.get("audio_base64") or b"")
    rate, nframes, pcm_len = wav_meta(raw)
    width = 2
    check(
        "1.首次合成 cloud 且 WAV 合法",
        spoken.get("engine") == "cloud" and rate == 24000 and nframes * width == pcm_len,
        f"engine={spoken.get('engine')} rate={rate} nframes={nframes} pcm={pcm_len}",
    )

    spoken2 = await synthesize(text, "ok", cfg)
    check(
        "2.第二次 cloud-cache",
        spoken2.get("engine") == "cloud-cache" and bool(spoken2.get("audio_base64")),
        f"engine={spoken2.get('engine')}",
    )

    cfg3 = copy.deepcopy(cfg)
    cloud3 = dict(cfg3["tts"]["cloud"])
    cloud3["base_url"] = "http://127.0.0.1:9"
    cfg3["tts"]["cloud"] = cloud3
    cfg3["tts"]["cache_dir"] = tempfile.mkdtemp(prefix="forkai_tts_miss_")
    timeout_s = float(cloud3.get("timeout_s", 1.5))
    t0 = time.monotonic()
    spoken3 = await synthesize("好的，后退", "ok", cfg3)
    elapsed = time.monotonic() - t0
    check(
        "3.云端不可达回退 piper",
        spoken3.get("engine") == "piper" and elapsed < timeout_s + 2,
        f"engine={spoken3.get('engine')} elapsed={elapsed:.2f}s timeout={timeout_s}",
    )

    sample = Path("/tmp/forkai-tts-samples/anna_u1.wav")
    if sample.is_file():
        fixed = normalize_wav(sample.read_bytes())
        rate4, nframes4, pcm4 = wav_meta(fixed)
        check(
            "4.normalize_wav 修正流式头",
            nframes4 > 0 and nframes4 * 2 == pcm4 and 0.5 < nframes4 / rate4 < 10,
            f"rate={rate4} nframes={nframes4} dur={nframes4 / rate4:.2f}s",
        )
    else:
        check("4.normalize_wav 修正流式头", False, f"样本不存在: {sample}")

    failed = [n for n, ok in RESULTS if not ok]
    print(f"[tts_cloud] {len(RESULTS) - len(failed)}/{len(RESULTS)} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
