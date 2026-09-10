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


def cloud_enabled(cfg: dict) -> bool:
    cloud = (cfg.get("tts") or {}).get("cloud") or {}
    if not cloud.get("enabled"):
        return False
    env_name = str(cloud.get("api_key_env") or "FORKAI_TTS_API_KEY")
    key = os.environ.get(env_name, "").strip()
    if key:
        return True
    global _warned_no_key
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
    cloud = (cfg.get("tts") or {}).get("cloud") or {}
    env_name = str(cloud.get("api_key_env") or "FORKAI_TTS_API_KEY")
    api_key = os.environ.get(env_name, "").strip()
    base_url = str(cloud.get("base_url") or "").rstrip("/")
    model = str(cloud.get("model") or "")
    voice = str(cloud.get("voice") or "anna")
    timeout_s = float(cloud.get("timeout_s", 1.5))
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
