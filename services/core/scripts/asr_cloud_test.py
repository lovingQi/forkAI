#!/usr/bin/env python3
"""云端 ASR 转写与超时失败（不回退 sherpa）。

前置：环境变量 FORKAI_TTS_API_KEY；可访问 SiliconFlow。

1. 默认模型对探针 WAV 在 timeout_s（5s）内出字
2. 不可达 base_url → ASRError
"""
import asyncio
import sys
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CORE_ROOT))

from app.asr.cloud import ASRError, current_asr_model, load_probe_wav, transcribe  # noqa: E402
from app.config import load_config  # noqa: E402


async def main() -> int:
    cfg = load_config()
    wav = load_probe_wav()
    if not wav:
        print("[asr_cloud_test] FAIL 缺少 assets/asr_probe.wav")
        return 1
    model = current_asr_model(cfg)
    text = await transcribe(cfg, wav)
    print(f"[asr_cloud_test] #1 {model} text={text!r} PASS")

    bad = {
        **cfg,
        "asr": {
            **(cfg.get("asr") or {}),
            "cloud": {
                **((cfg.get("asr") or {}).get("cloud") or {}),
                "base_url": "http://127.0.0.1:1",
                "timeout_s": 2,
            },
        },
    }
    try:
        await transcribe(bad, wav)
        print("[asr_cloud_test] #2 不可达未抛错 FAIL")
        return 1
    except ASRError as e:
        print(f"[asr_cloud_test] #2 unreachable → {e} PASS")
    print("[asr_cloud_test] 总体 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
