"""内置 TTS：直接调 piper CLI 合成中文 wav（对齐 services/speech/src/index.ts 的 /tts/speak）。

- subprocess 异步调 piper：--model/--config/--output_file，stdin 写文本，
  LD_LIBRARY_PATH 指向 piper 库目录。
- piper 不可用（二进制/模型缺失）或合成失败时回退 mock：audio_base64=None, engine="mock"。
- rate 规则：fail=0.9, wake=1.1, 其他=1.05。
- 提示音（步骤33）：speak.beep=true 时，style=ok 前缀拼 beep_ok、style=fail 拼
  beep_fail（纯 PCM 拼接后重写 wav 头，参数与 piper 输出一致）；wake 不拼；
  mock 回退（audio_base64=None）不受影响。
- 拉丁字母转写（_LETTER_ZH）：zh_CN-huayan 无英文字母发音规则（"p1点"会念成"崩点"），
  进 piper 前把字母换成中文读音字；仅影响音频，返回的 text 字段保持原文。
"""
import asyncio
import base64
import io
import os
import random
import re
import string
import tempfile
import wave

from ..config import CORE_ROOT, now_ms

_RATE = {"fail": 0.9, "wake": 1.1}
_RATE_DEFAULT = 1.05

# 拉丁字母 → 中文读音（工业习惯读法；数字不动，piper 数字发音已实测正常）
_LETTER_ZH = {
    "a": "诶", "b": "比", "c": "西", "d": "低", "e": "衣", "f": "爱福",
    "g": "记", "h": "爱尺", "i": "爱", "j": "勾", "k": "开", "l": "爱乐",
    "m": "爱姆", "n": "恩", "o": "欧", "p": "批", "q": "丘", "r": "阿尔",
    "s": "爱思", "t": "提", "u": "优", "v": "维", "w": "达不溜", "x": "爱克斯",
    "y": "歪", "z": "贼",
}


def _tts_normalize(text: str) -> str:
    """进 piper 前的文本归一化：拉丁字母 → 中文读音字（大小写均可）。"""
    return re.sub(r"[A-Za-z]", lambda m: _LETTER_ZH[m.group(0).lower()], text)

_BEEP_FILE = {"ok": "beep_ok.wav", "fail": "beep_fail.wav"}
_beep_cache: dict = {}


def _resolve(cfg: dict) -> dict:
    p = cfg.get("piper", {})
    return {
        "bin": CORE_ROOT / p.get("bin", "models/piper/piper/piper"),
        "lib_dir": CORE_ROOT / p.get("libDir", "models/piper/piper"),
        "model": CORE_ROOT / p.get("model", "models/piper/zh_CN-huayan-medium.onnx"),
        "config": CORE_ROOT
        / p.get("config", "models/piper/zh_CN-huayan-medium.onnx.json"),
    }


def piper_available(cfg: dict) -> bool:
    p = _resolve(cfg)
    return (
        p["bin"].is_file()
        and p["model"].is_file()
        and p["config"].is_file()
    )


def _load_beep(name: str):
    """读 assets 下提示音（缓存），返回 (params, pcm)。"""
    if name not in _beep_cache:
        with wave.open(str(CORE_ROOT / "assets" / name), "rb") as w:
            _beep_cache[name] = (w.getparams(), w.readframes(w.getnframes()))
    return _beep_cache[name]


def _prepend_beep(wav_bytes: bytes, style: str) -> bytes:
    """纯 PCM 前缀拼接提示音并重写 wav 头；params 不一致或文件缺失则原样返回。"""
    beep_name = _BEEP_FILE.get(style)
    if not beep_name:
        return wav_bytes
    try:
        beep_params, beep_pcm = _load_beep(beep_name)
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            speech_params = w.getparams()
            speech_pcm = w.readframes(w.getnframes())
        if (
            beep_params.framerate != speech_params.framerate
            or beep_params.sampwidth != speech_params.sampwidth
            or beep_params.nchannels != speech_params.nchannels
        ):
            print("[forkai-core] beep 与 piper 输出 params 不一致，跳过提示音")
            return wav_bytes
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setparams(speech_params)
            w.writeframes(beep_pcm + speech_pcm)
        return out.getvalue()
    except Exception as e:
        print(f"[forkai-core] beep 拼接失败，原样返回: {e}")
        return wav_bytes


_LEAD_SILENCE_MS_DEFAULT = 250


def _prepend_silence(wav_bytes: bytes, ms: int) -> bytes:
    """播报最前面拼 ms 毫秒静音垫（纯 PCM 拼接后重写 wav 头，参数与 piper 输出一致）。
    浏览器/音箱/功放从休眠到出声有百毫秒级建立延迟，前导垫把这段让出来，
    避免正文开头一两个字被吃。失败原样返回。"""
    if ms <= 0:
        return wav_bytes
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            params = w.getparams()
            pcm = w.readframes(w.getnframes())
        n = int(params.framerate * ms / 1000)
        silence = b"\x00" * (n * params.sampwidth * params.nchannels)
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setparams(params)
            w.writeframes(silence + pcm)
        return out.getvalue()
    except Exception as e:
        print(f"[forkai-core] 前导静音拼接失败，原样返回: {e}")
        return wav_bytes


async def _synthesize_with_piper(cfg: dict, text: str) -> bytes:
    """调 piper CLI 合成，返回 wav 字节；失败抛异常由调用方回退。"""
    p = _resolve(cfg)
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    out_file = os.path.join(tempfile.gettempdir(), f"forkai_tts_{now_ms()}_{rand}.wav")
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = f"{p['lib_dir']}:{os.environ.get('LD_LIBRARY_PATH', '')}"
    try:
        proc = await asyncio.create_subprocess_exec(
            str(p["bin"]),
            "--model",
            str(p["model"]),
            "--config",
            str(p["config"]),
            "--output_file",
            out_file,
            stdin=asyncio.subprocess.PIPE,
            env=env,
        )
        await proc.communicate(_tts_normalize(text).encode("utf-8"))
        if proc.returncode != 0:
            raise RuntimeError(f"piper exit {proc.returncode}")
        with open(out_file, "rb") as f:
            return f.read()
    finally:
        try:
            os.unlink(out_file)
        except OSError:
            pass


async def synthesize(text: str, style: str, cfg: dict) -> dict:
    rate = _RATE.get(style, _RATE_DEFAULT)
    if piper_available(cfg) and text:
        try:
            wav_bytes = await _synthesize_with_piper(cfg, text)
            if (cfg.get("speak") or {}).get("beep", True):
                wav_bytes = _prepend_beep(wav_bytes, style)
            lead_ms = int((cfg.get("speak") or {}).get("leadSilenceMs", _LEAD_SILENCE_MS_DEFAULT))
            wav_bytes = _prepend_silence(wav_bytes, lead_ms)
            return {
                "text": text,
                "style": style,
                "rate": rate,
                "voice": "zh_CN-huayan-medium",
                "audio_base64": base64.b64encode(wav_bytes).decode("ascii"),
                "engine": "piper",
            }
        except Exception as e:
            print(f"[forkai-core] piper 合成失败，回退 mock: {e}")
    return {
        "text": text,
        "style": style,
        "rate": rate,
        "voice": "calm-female-zh",
        "audio_base64": None,
        "engine": "mock",
        "note": "piper unavailable; UI fallback to speechSynthesis",
    }
