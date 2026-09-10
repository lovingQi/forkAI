"""云端 ASR（SiliconFlow OpenAI 兼容 /audio/transcriptions）。

PTT 松手后整句 WAV 上传；超时/失败抛 ASRError，不回退 sherpa。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

from ..config import CORE_ROOT

_http: httpx.AsyncClient | None = None
_warned_no_key = False

PROBE_WAV = CORE_ROOT / "assets" / "asr_probe.wav"


def _asr_log(msg: str) -> None:
    print(f"[forkai-core] ASR {msg}", flush=True)


class ASRError(Exception):
    """云端识别失败（超时、HTTP、空文本、缺 key）。"""


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient()
    return _http


async def close_asr_client() -> None:
    global _http
    if _http is not None:
        await _http.aclose()
        _http = None


def _cloud_cfg(cfg: dict) -> dict:
    return ((cfg.get("asr") or {}).get("cloud") or {})


def _api_key(cfg: dict) -> str:
    env_name = str(_cloud_cfg(cfg).get("api_key_env") or "FORKAI_TTS_API_KEY")
    return os.environ.get(env_name, "").strip()


def cloud_asr_enabled(cfg: dict) -> bool:
    cloud = _cloud_cfg(cfg)
    if not cloud.get("enabled", True):
        return False
    key = _api_key(cfg)
    if key:
        return True
    global _warned_no_key
    if not _warned_no_key:
        env_name = str(cloud.get("api_key_env") or "FORKAI_TTS_API_KEY")
        print(f"[forkai-core] ASR 云端已启用但环境变量 {env_name} 为空")
        _warned_no_key = True
    return False


def current_asr_model(cfg: dict) -> str:
    return str(_cloud_cfg(cfg).get("model") or "Qwen/Qwen3-ASR-1.7B")


def asr_display_name(model_id: str) -> str:
    return (model_id or "").rsplit("/", 1)[-1] or model_id


async def list_asr_models(cfg: dict) -> list[str]:
    key = _api_key(cfg)
    if not key:
        raise ASRError("asr_no_key")
    cloud = _cloud_cfg(cfg)
    base = str(cloud.get("base_url") or "https://api.siliconflow.cn/v1").rstrip("/")
    res = await _client().get(
        f"{base}/models",
        headers={"Authorization": f"Bearer {key}"},
        params={"type": "audio", "sub_type": "speech-to-text"},
        timeout=10.0,
    )
    if not 200 <= res.status_code < 300:
        raise ASRError(f"asr_list_{res.status_code}")
    data = res.json().get("data") or []
    ids = []
    for item in data:
        mid = item.get("id") if isinstance(item, dict) else None
        if isinstance(mid, str) and mid and mid not in ids:
            ids.append(mid)
    return ids


async def transcribe(
    cfg: dict, wav_bytes: bytes, model: str | None = None, *, log: bool = True
) -> str:
    if not wav_bytes:
        if log:
            _asr_log("fail reason=empty_audio")
        raise ASRError("empty_audio")
    key = _api_key(cfg)
    if not key:
        if log:
            _asr_log("fail reason=no_key")
        raise ASRError("asr_no_key")
    cloud = _cloud_cfg(cfg)
    base = str(cloud.get("base_url") or "https://api.siliconflow.cn/v1").rstrip("/")
    timeout_s = float(cloud.get("timeout_s", 5))
    use_model = model or current_asr_model(cfg)
    t0 = time.perf_counter()
    try:
        res = await _client().post(
            f"{base}/audio/transcriptions",
            headers={"Authorization": f"Bearer {key}"},
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data={"model": use_model},
            timeout=timeout_s,
        )
    except httpx.TimeoutException as e:
        ms = int((time.perf_counter() - t0) * 1000)
        if log:
            _asr_log(
                f"fail reason=timeout model={use_model} "
                f"wav_bytes={len(wav_bytes)} timeout_s={timeout_s} elapsed_ms={ms}"
            )
        raise ASRError("asr_timeout") from e
    except httpx.HTTPError as e:
        ms = int((time.perf_counter() - t0) * 1000)
        if log:
            _asr_log(
                f"fail reason=http_error model={use_model} "
                f"wav_bytes={len(wav_bytes)} elapsed_ms={ms} err={type(e).__name__}"
            )
        raise ASRError("asr_http") from e
    ms = int((time.perf_counter() - t0) * 1000)
    if not 200 <= res.status_code < 300:
        body = (res.text or "")[:240].replace("\n", " ")
        if log:
            _asr_log(
                f"fail reason=http_{res.status_code} model={use_model} "
                f"wav_bytes={len(wav_bytes)} elapsed_ms={ms} body={body!r}"
            )
        raise ASRError(f"asr_http_{res.status_code}")
    try:
        text = str(res.json().get("text") or "").strip()
    except Exception as e:
        if log:
            _asr_log(
                f"fail reason=parse model={use_model} "
                f"wav_bytes={len(wav_bytes)} elapsed_ms={ms} err={e}"
            )
        raise ASRError("asr_parse") from e
    if not text:
        if log:
            _asr_log(
                f"fail reason=empty_transcription model={use_model} "
                f"wav_bytes={len(wav_bytes)} elapsed_ms={ms}"
            )
        raise ASRError("empty_transcription")
    if log:
        _asr_log(
            f"ok model={use_model} wav_bytes={len(wav_bytes)} "
            f"elapsed_ms={ms} chars={len(text)}"
        )
    return text


async def probe_asr_model(cfg: dict, model: str, wav_bytes: bytes) -> dict:
    if not wav_bytes:
        return {
            "id": model,
            "name": asr_display_name(model),
            "latencyMs": None,
            "error": "无探针音频",
        }
    t0 = time.perf_counter()
    try:
        await transcribe(cfg, wav_bytes, model, log=False)
        ms = int((time.perf_counter() - t0) * 1000)
        return {"id": model, "name": asr_display_name(model), "latencyMs": ms, "error": None}
    except ASRError as e:
        err = str(e)
        if err == "asr_timeout":
            err = "超时"
        return {
            "id": model,
            "name": asr_display_name(model),
            "latencyMs": None,
            "error": err,
        }


def load_probe_wav() -> bytes:
    p = PROBE_WAV
    if not p.is_file():
        return b""
    return Path(p).read_bytes()
