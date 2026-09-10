#!/usr/bin/env python3
"""预热 TTS 云端缓存。

前置：网络可达；环境变量 FORKAI_TTS_API_KEY 已设置；可在无 core 进程时单独跑。

用法（services/core 目录下）：
    .venv/bin/python scripts/tts_prewarm.py
"""
import asyncio
import sys
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CORE_ROOT))

from app.config import load_config  # noqa: E402
from app.taskflow.store import FlowStore  # noqa: E402
from app.tts.prewarm import prewarm  # noqa: E402


async def main() -> int:
    cfg = load_config()
    store = FlowStore()
    stop = asyncio.Event()
    stats = await prewarm(cfg, store, stop)
    print(
        f"[tts_prewarm] total={stats['total']} hit={stats['hit']} "
        f"synthesized={stats['synthesized']} failed={stats['failed']}"
    )
    return 0 if stats["failed"] == 0 or stats["synthesized"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
