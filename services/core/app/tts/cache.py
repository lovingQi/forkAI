"""TTS 音频磁盘缓存：键 = sha1(model|voice|text)。"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ..config import CORE_ROOT

_put_count = 0


def cache_dir(cfg: dict) -> Path:
    rel = ((cfg.get("tts") or {}).get("cache_dir")) or "data/tts_cache"
    p = Path(rel)
    d = p if p.is_absolute() else (CORE_ROOT / p)
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_key(model: str, voice: str, text: str) -> str:
    raw = f"{model}|{voice}|{text}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def cache_get(cfg: dict, key: str) -> bytes | None:
    path = cache_dir(cfg) / f"{key}.wav"
    try:
        return path.read_bytes()
    except OSError:
        return None


def cache_put(cfg: dict, key: str, data: bytes) -> None:
    global _put_count
    d = cache_dir(cfg)
    dest = d / f"{key}.wav"
    tmp = d / f"{key}.wav.tmp"
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    _put_count += 1
    if _put_count % 50 == 0:
        _evict(cfg, d)


def _evict(cfg: dict, d: Path) -> None:
    max_files = int((cfg.get("tts") or {}).get("cache_max_files", 5000))
    files = [p for p in d.glob("*.wav") if p.is_file()]
    extra = len(files) - max_files
    if extra <= 0:
        return
    files.sort(key=lambda p: p.stat().st_mtime)
    for p in files[:extra]:
        try:
            p.unlink()
        except OSError:
            pass
