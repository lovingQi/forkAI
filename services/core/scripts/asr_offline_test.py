#!/usr/bin/env python3
"""步骤13 离线验证：piper 合成语音 → sherpa-onnx 流式识别。

用法（在 services/core 目录下）：
    .venv/bin/python scripts/asr_offline_test.py

流程：用内置 piper（models/piper）合成 "前进" / "电量多少" 的 wav，
重采样到 16kHz 后按 100ms 块喂给 SherpaASR 流式识别，输出识别文本、耗时与 RTF。
验收：识别结果包含目标文本，且 RTF 明显小于 1。
"""
import asyncio
import base64
import io
import sys
import time
import wave
from pathlib import Path

import numpy as np

CORE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CORE_ROOT))

from app.config import load_config  # noqa: E402
from app.tts.piper import synthesize  # noqa: E402

TARGETS = ["前进", "电量多少"]


def wav_base64_to_pcm16_16k(audio_base64: str) -> bytes:
    """piper wav (22050Hz int16 mono) → 16kHz int16 mono PCM（线性插值重采样）。"""
    raw = base64.b64decode(audio_base64)
    with wave.open(io.BytesIO(raw), "rb") as w:
        assert w.getsampwidth() == 2 and w.getnchannels() == 1
        src_rate = w.getframerate()
        samples = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    if src_rate != 16000:
        duration = len(samples) / src_rate
        n_out = int(duration * 16000)
        x_old = np.linspace(0, duration, num=len(samples), endpoint=False)
        x_new = np.linspace(0, duration, num=n_out, endpoint=False)
        samples = np.interp(x_new, x_old, samples).astype(np.int16)
    return samples.tobytes()


async def recognize(cfg: dict, asr, pcm: bytes) -> tuple[str, float, float]:
    """按 100ms 块流式喂 PCM，返回 (识别文本, 音频时长s, 推理耗时s)。"""
    from app.asr.stream import ASRStream

    stream = ASRStream(asr)
    block = int(16000 * 2 * 0.1)  # 100ms * 16kHz * 2 bytes
    audio_seconds = len(pcm) / 2 / 16000
    t0 = time.perf_counter()
    for off in range(0, len(pcm), block):
        await stream.feed(pcm[off : off + block])
    text = await stream.flush()
    elapsed = time.perf_counter() - t0
    await stream.close()
    return text.strip(), audio_seconds, elapsed


async def main() -> int:
    cfg = load_config()

    from app.asr.engine import SherpaASR

    t0 = time.perf_counter()
    asr = await SherpaASR.instance(cfg)
    print(f"[asr_test] 模型加载 {time.perf_counter() - t0:.2f}s")

    ok = True
    for target in TARGETS:
        spoken = await synthesize(target, "ok", cfg)
        if not spoken.get("audio_base64"):
            print(f"[asr_test] piper 合成失败（{target}），无法测试")
            ok = False
            continue
        pcm = wav_base64_to_pcm16_16k(spoken["audio_base64"])
        text, audio_s, infer_s = await recognize(cfg, asr, pcm)
        from app.nlu.rules import correct_asr

        corrected = correct_asr(text)
        rtf = infer_s / audio_s if audio_s > 0 else float("inf")
        hit = target in corrected
        ok = ok and hit and rtf < 1
        print(
            f"[asr_test] 目标={target!r} 识别={text!r} 纠偏={corrected!r} "
            f"音频={audio_s:.2f}s 耗时={infer_s:.2f}s RTF={rtf:.3f} "
            f"{'PASS' if hit and rtf < 1 else 'FAIL'}"
        )
    print("[asr_test] 总体:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
