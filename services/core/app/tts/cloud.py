"""云端 CosyVoice2 合成（SiliconFlow OpenAI 兼容 /audio/speech）。

- 模块级懒建 httpx.AsyncClient，跨句复用连接。
- 返回 WAV；厂商流式头 data 长度非法时由 normalize_wav 按实际 PCM 重写。
"""
from __future__ import annotations

import io
import os
import struct
import wave

import httpx

from ..g2a import (
    G2A_TTS_MODELS,
    G2A_TTS_VOICES,
    g2a_base,
    g2a_enabled,
    g2a_key,
    g2a_language,
    g2a_timeout,
    g2a_tts_model,
    g2a_tts_voice,
    g2a_voice_name,
    http_client as g2a_http,
    is_g2a_tts,
)

_http: httpx.AsyncClient | None = None
_warned_no_key = False


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient()
    return _http


async def close_client() -> None:
    global _http
    if _http is not None:
        await _http.aclose()
        _http = None


def current_tts_model(cfg: dict) -> str:
    tts = cfg.get("tts") or {}
    selected = str(tts.get("model") or "").strip()
    if selected:
        return selected
    return str((tts.get("cloud") or {}).get("model") or "FunAudioLLM/CosyVoice2-0.5B")


def tts_display_name(model_id: str) -> str:
    mid = (model_id or "").strip()
    if mid == "grok-voice-latest":
        return "Grok Voice"
    if mid == "grok-voice-think-fast-1.0":
        return "Grok Voice Fast 1.0"
    if mid == "grok-voice-think-fast-2.0":
        return "Grok Voice Fast 2.0"
    if is_g2a_tts(mid):
        return mid
    return mid.rsplit("/", 1)[-1] or mid


def list_tts_models(cfg: dict) -> list[str]:
    ids: list[str] = []
    sf = str(((cfg.get("tts") or {}).get("cloud") or {}).get("model") or "")
    if sf:
        ids.append(sf)
    if g2a_enabled(cfg):
        for mid in (g2a_tts_model(cfg), *G2A_TTS_MODELS):
            if mid and mid not in ids:
                ids.append(mid)
    selected = current_tts_model(cfg)
    if selected and selected in ids:
        ids = [mid for mid in ids if mid != selected]
        ids.insert(0, selected)
    elif selected:
        ids.insert(0, selected)
    return ids


def current_tts_voice(cfg: dict) -> str:
    if is_g2a_tts(current_tts_model(cfg)):
        vid = g2a_tts_voice(cfg)
        allowed = {oid for oid, _ in G2A_TTS_VOICES}
        return vid if vid in allowed else "eve"
    return str(((cfg.get("tts") or {}).get("cloud") or {}).get("voice") or "anna")


def list_tts_voices(cfg: dict) -> list[dict]:
    if is_g2a_tts(current_tts_model(cfg)):
        items = [{"id": oid, "name": name} for oid, name in G2A_TTS_VOICES]
        selected = current_tts_voice(cfg)
        if selected and selected not in {it["id"] for it in items}:
            items.insert(0, {"id": selected, "name": g2a_voice_name(selected)})
        return items
    vid = current_tts_voice(cfg)
    return [{"id": vid, "name": vid}]


def cloud_enabled(cfg: dict) -> bool:
    global _warned_no_key
    if is_g2a_tts(current_tts_model(cfg)):
        if g2a_enabled(cfg) and g2a_key(cfg):
            return True
        if not _warned_no_key:
            print("[forkai-core] TTS 已选 Grok2API 但 FORKAI_G2A_API_KEY 为空，回退 piper")
            _warned_no_key = True
        return False
    cloud = (cfg.get("tts") or {}).get("cloud") or {}
    if not cloud.get("enabled"):
        return False
    env_name = str(cloud.get("api_key_env") or "FORKAI_TTS_API_KEY")
    key = os.environ.get(env_name, "").strip()
    if key:
        return True
    if not _warned_no_key:
        print(f"[forkai-core] TTS 云端已启用但环境变量 {env_name} 为空，回退 piper")
        _warned_no_key = True
    return False


def normalize_wav(data: bytes) -> bytes:
    """定位 data 块，按实际字节数用 wave 重写头；非 RIFF 抛 ValueError。"""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not riff wav")
    idx = data.find(b"data")
    if idx < 0 or idx + 8 > len(data):
        raise ValueError("no data chunk")
    pcm = data[idx + 8 :]
    channels = struct.unpack_from("<H", data, 22)[0]
    rate = struct.unpack_from("<I", data, 24)[0]
    bits = struct.unpack_from("<H", data, 34)[0]
    if channels < 1 or rate < 1 or bits not in (8, 16, 24, 32) or not pcm:
        raise ValueError("invalid wav fmt")
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(bits // 8)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out.getvalue()


async def synthesize_cloud(cfg: dict, text: str) -> bytes:
    if is_g2a_tts(current_tts_model(cfg)):
        return await _synthesize_g2a(cfg, text)
    cloud = (cfg.get("tts") or {}).get("cloud") or {}
    env_name = str(cloud.get("api_key_env") or "FORKAI_TTS_API_KEY")
    api_key = os.environ.get(env_name, "").strip()
    base_url = str(cloud.get("base_url") or "").rstrip("/")
    model = str(cloud.get("model") or "")
    voice = str(cloud.get("voice") or "anna")
    timeout_s = float(cloud.get("timeout_s", 5))
    res = await _client().post(
        f"{base_url}/audio/speech",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "input": text,
            "voice": f"{model}:{voice}",
            "response_format": "wav",
            "speed": 1.0,
        },
        timeout=timeout_s,
    )
    if not 200 <= res.status_code < 300:
        raise RuntimeError(f"tts cloud {res.status_code}")
    return normalize_wav(res.content)


async def _synthesize_g2a(cfg: dict, text: str) -> bytes:
    key = g2a_key(cfg)
    if not key:
        raise RuntimeError("tts g2a no_key")
    model = current_tts_model(cfg)
    res = await g2a_http().post(
        f"{g2a_base(cfg)}/audio/speech",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "input": text,
            "voice": g2a_tts_voice(cfg),
            "response_format": "wav",
            "language": g2a_language(cfg),
        },
        timeout=g2a_timeout(cfg, 20.0),
    )
    if not 200 <= res.status_code < 300:
        raise RuntimeError(f"tts g2a {res.status_code}")
    return normalize_wav(res.content)
