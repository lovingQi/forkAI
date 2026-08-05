#!/usr/bin/env python3
"""生成提示音 assets/beep_ok.wav 与 beep_fail.wav（步骤33）。

采样率/位宽/声道与 piper 输出一致（先用 piper 合成一个字读取其 wav params），
保证后续纯 PCM 拼接时参数匹配。
- beep_ok:   880Hz 正弦 120ms，10ms 线性淡入淡出
- beep_fail: 220Hz 正弦 300ms，10ms 线性淡入淡出

用法（services/core 目录下）：.venv/bin/python scripts/gen_beeps.py
"""
import asyncio
import io
import math
import struct
import sys
import wave
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CORE_ROOT))

from app.config import load_config  # noqa: E402
from app.tts.piper import _synthesize_with_piper  # noqa: E402

ASSETS = CORE_ROOT / "assets"


def gen_tone(freq: float, ms: int, rate: int, fade_ms: int = 10, amp: float = 0.6) -> bytes:
    n = int(rate * ms / 1000)
    fade = int(rate * fade_ms / 1000)
    frames = bytearray()
    for i in range(n):
        gain = 1.0
        if i < fade:
            gain = i / fade
        elif i >= n - fade:
            gain = (n - i) / fade
        v = int(amp * gain * 32767 * math.sin(2 * math.pi * freq * i / rate))
        frames += struct.pack("<h", v)
    return bytes(frames)


async def main() -> None:
    cfg = load_config()
    # 读一次 piper 输出确认 params（采样率/位宽/声道）
    wav_bytes = await _synthesize_with_piper(cfg, "滴")
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        rate, width, channels = w.getframerate(), w.getsampwidth(), w.getnchannels()
    print(f"[gen_beeps] piper params: rate={rate} width={width} channels={channels}")
    assert width == 2 and channels == 1, "piper 输出非 16bit mono，需调整生成逻辑"

    ASSETS.mkdir(exist_ok=True)
    for name, freq, ms in [("beep_ok.wav", 880, 120), ("beep_fail.wav", 220, 300)]:
        pcm = gen_tone(freq, ms, rate)
        out = ASSETS / name
        with wave.open(str(out), "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(width)
            w.setframerate(rate)
            w.writeframes(pcm)
        print(f"[gen_beeps] 写出 {out} ({ms}ms {freq}Hz)")


if __name__ == "__main__":
    asyncio.run(main())
