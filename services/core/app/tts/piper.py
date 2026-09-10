"""内置 TTS：直接调 piper CLI 合成中文 wav（兜底路径）。

- subprocess 异步调 piper：--model/--config/--output_file，stdin 写文本，
  LD_LIBRARY_PATH 指向 piper 库目录。
- piper 不可用（二进制/模型缺失）或合成失败时回退 mock：audio_base64=None, engine="mock"。
- rate 规则：fail=0.9, wake=1.1, 其他=1.05（仅元数据，不传给 piper CLI）。
- 拉丁字母转写（_LETTER_ZH）：zh_CN-huayan 无英文字母发音规则（"p1点"会念成"崩点"），
  进 piper 前把字母换成中文读音字；仅影响音频，返回的 text 字段保持原文。
"""
import asyncio
import base64
import os
import random
import re
import string
import tempfile

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


async def synthesize_piper(text: str, style: str, cfg: dict) -> dict:
    rate = _RATE.get(style, _RATE_DEFAULT)
    if piper_available(cfg) and text:
        try:
            wav_bytes = await _synthesize_with_piper(cfg, text)
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
