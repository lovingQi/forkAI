"""TTS 合成入口：本地缓存 → 云端 CosyVoice2 → piper → mock。"""
from __future__ import annotations

import base64

from .cache import cache_get, cache_key, cache_put
from .cloud import cloud_enabled, synthesize_cloud
from .piper import _RATE, _RATE_DEFAULT, synthesize_piper


def _mock(text: str, style: str) -> dict:
    return {
        "text": text,
        "style": style,
        "rate": _RATE.get(style, _RATE_DEFAULT),
        "voice": "calm-female-zh",
        "audio_base64": None,
        "engine": "mock",
        "note": "piper unavailable; UI fallback to speechSynthesis",
    }


def _ok(text: str, style: str, voice: str, engine: str, wav_bytes: bytes) -> dict:
    return {
        "text": text,
        "style": style,
        "rate": _RATE.get(style, _RATE_DEFAULT),
        "voice": voice,
        "engine": engine,
        "audio_base64": base64.b64encode(wav_bytes).decode("ascii"),
    }


def _cloud_ids(cfg: dict) -> tuple[str, str]:
    cloud = (cfg.get("tts") or {}).get("cloud") or {}
    return str(cloud.get("model") or ""), str(cloud.get("voice") or "anna")


def _log_tts(spoken: dict) -> dict:
    print(
        f"[forkai-core] TTS engine={spoken.get('engine')} "
        f"voice={spoken.get('voice')} chars={len(spoken.get('text') or '')}",
        flush=True,
    )
    return spoken


async def synthesize(text: str, style: str, cfg: dict) -> dict:
    if not (text or "").strip():
        return _log_tts(_mock(text, style))
    model, voice = _cloud_ids(cfg)
    key = cache_key(model, voice, text)
    cached = cache_get(cfg, key)
    if cached:
        return _log_tts(_ok(text, style, voice, "cloud-cache", cached))
    if cloud_enabled(cfg):
        try:
            wav_bytes = await synthesize_cloud(cfg, text)
            cache_put(cfg, key, wav_bytes)
            return _log_tts(_ok(text, style, voice, "cloud", wav_bytes))
        except Exception as e:
            print(f"[forkai-core] TTS 云端失败，回退 piper: {e}", flush=True)
    return _log_tts(await synthesize_piper(text, style, cfg))
