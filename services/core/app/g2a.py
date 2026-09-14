"""自建 Grok2API（OpenAI 兼容 /v1/audio/*）备选 ASR/TTS。

出站不走系统代理，避免 HTTP_PROXY 把公网 VPS 请求拖死。
"""
from __future__ import annotations

import os

import httpx

G2A_ASR_MODEL = "grok-stt"
G2A_TTS_MODEL = "grok-voice-latest"
G2A_TTS_VOICE = "eve"
G2A_TTS_MODELS = (
    "grok-voice-latest",
    "grok-voice-think-fast-1.0",
    "grok-voice-think-fast-2.0",
)
# 与 GET /v1/tts/voices 对齐；live 拉取失败时用此表
G2A_TTS_VOICES: tuple[tuple[str, str], ...] = (
    ("altair", "Altair"),
    ("ara", "Ara"),
    ("atlas", "Atlas"),
    ("aurora", "Aurora"),
    ("carina", "Carina"),
    ("castor", "Castor"),
    ("celeste", "Celeste"),
    ("cosmo", "Cosmo"),
    ("eve", "Eve"),
    ("helios", "Helios"),
    ("helix", "Helix"),
    ("iris", "Iris"),
    ("kepler", "Kepler"),
    ("leo", "Leo"),
    ("liora", "Liora"),
    ("lumen", "Lumen"),
    ("luna", "Luna"),
    ("lux", "Lux"),
    ("naksh", "Naksh"),
    ("orion", "Orion"),
    ("perseus", "Perseus"),
    ("rex", "Rex"),
    ("rigel", "Rigel"),
    ("sal", "Sal"),
    ("sirius", "Sirius"),
    ("ursa", "Ursa"),
    ("zagan", "Zagan"),
    ("zenith", "Zenith"),
)
_DEFAULT_BASE = "http://45.76.71.110:8001/v1"

_http: httpx.AsyncClient | None = None


def http_client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(trust_env=False)
    return _http


async def close_g2a_client() -> None:
    global _http
    if _http is not None:
        await _http.aclose()
        _http = None


def g2a_cfg(cfg: dict) -> dict:
    return (cfg or {}).get("grok2api") or {}


def g2a_enabled(cfg: dict) -> bool:
    g = g2a_cfg(cfg)
    if g.get("enabled") is False:
        return False
    return True


def g2a_key(cfg: dict) -> str:
    env_name = str(g2a_cfg(cfg).get("api_key_env") or "FORKAI_G2A_API_KEY")
    return os.environ.get(env_name, "").strip()


def g2a_base(cfg: dict) -> str:
    return str(g2a_cfg(cfg).get("base_url") or _DEFAULT_BASE).rstrip("/")


def g2a_language(cfg: dict) -> str:
    return str(g2a_cfg(cfg).get("language") or "zh")


def g2a_timeout(cfg: dict, default: float = 20.0) -> float:
    try:
        return float(g2a_cfg(cfg).get("timeout_s", default))
    except (TypeError, ValueError):
        return default


def g2a_asr_model(cfg: dict) -> str:
    return str(g2a_cfg(cfg).get("asr_model") or G2A_ASR_MODEL)


def g2a_tts_model(cfg: dict) -> str:
    return str(g2a_cfg(cfg).get("tts_model") or G2A_TTS_MODEL)


def g2a_tts_voice(cfg: dict) -> str:
    tts = (cfg or {}).get("tts") or {}
    selected = str(tts.get("voice") or "").strip().lower()
    if selected:
        return selected
    return str(g2a_cfg(cfg).get("tts_voice") or G2A_TTS_VOICE).strip().lower() or G2A_TTS_VOICE


def g2a_voice_name(voice_id: str) -> str:
    vid = (voice_id or "").strip().lower()
    for oid, name in G2A_TTS_VOICES:
        if oid == vid:
            return name
    return (voice_id or "").strip() or vid


def is_g2a_asr(model: str) -> bool:
    m = (model or "").strip().lower()
    return m == G2A_ASR_MODEL or m.startswith("grok-stt")


def is_g2a_tts(model: str) -> bool:
    return "grok-voice" in (model or "").strip().lower()
