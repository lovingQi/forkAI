"""16kHz mono PCM16 little-endian → WAV 容器（无 ffmpeg）。"""
import array
import io
import wave


def pcm16_to_wav(pcm: bytes, sample_rate: int = 16000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return out.getvalue()


def pcm16_stats(pcm: bytes, sample_rate: int = 16000) -> dict:
    """PTT 诊断：时长与峰值（不解码内容）。"""
    n = len(pcm) // 2
    ms = int(n * 1000 / sample_rate) if sample_rate > 0 else 0
    peak = 0
    if n:
        samples = array.array("h")
        samples.frombytes(pcm[: n * 2])
        peak = max(abs(s) for s in samples)
    return {"bytes": len(pcm), "ms": ms, "peak": peak}
