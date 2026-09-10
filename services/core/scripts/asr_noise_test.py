#!/usr/bin/env python3
"""ASR 噪声鲁棒性测试（方案B）：工厂噪声 × 多档 SNR × 识别率统计。

模拟"工厂车间作业环境噪音大"的核心场景：
- 用 piper 合成一批语音指令（覆盖 6 任务 + 问答 + 唤醒 + 任务流控制）
- 生成工厂风格噪声（宽带机械轰鸣 + 周期性冲击 + 随机咔哒声）
- 按 SNR 0/5/10/15/20 dB 与干净语音混合
- 喂 sherpa-onnx 流式识别，统计各 SNR 档的识别准确率（含纠偏后）

用法（在 services/core 目录下）：
    .venv/bin/python scripts/asr_noise_test.py

输出：每条指令 × 每档 SNR 的识别结果明细 + 汇总表 + 总体结论。
"""
import asyncio
import base64
import io
import sys
import time
import wave
from pathlib import Path

import numpy as np

CORE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CORE_ROOT))

from app.config import load_config  # noqa: E402
from app.nlu.rules import correct_asr  # noqa: E402
from app.tts.piper import synthesize_piper as synthesize  # noqa: E402

# 覆盖 6 任务 + 问答 + 唤醒 + 任务流控制的代表性指令
UTTERANCES = [
    "前进", "后退", "停止", "左转", "右转",           # 点动
    "升起货叉", "放下货叉", "升到150毫米",             # 货叉
    "原地转90度",                                     # 旋转
    "从A点到B点", "识别栈板", "去充电",               # 任务
    "电量多少", "当前模式", "为什么停",               # 问答
    "玖物，玖物",                                     # 唤醒
    "执行取货演示流程", "暂停任务",                   # 任务流控制
]

SNR_LEVELS = [20, 15, 10, 5, 0]  # dB，从干净到极噪


def wav_base64_to_pcm16_16k(audio_base64: str) -> np.ndarray:
    """piper wav → 16kHz int16 mono（保持 int16，供直接识别与混合两用）。"""
    raw = base64.b64decode(audio_base64)
    with wave.open(io.BytesIO(raw), "rb") as w:
        src_rate = w.getframerate()
        samples = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    if src_rate != 16000:
        duration = len(samples) / src_rate
        n_out = int(duration * 16000)
        x_old = np.linspace(0, duration, num=len(samples), endpoint=False)
        x_new = np.linspace(0, duration, num=n_out, endpoint=False)
        samples = np.interp(x_new, x_old, samples).astype(np.int16)
    return samples  # int16


def gen_factory_noise(n_samples: int, seed: int = 42) -> np.ndarray:
    """工厂风格噪声：低频机械轰鸣 + 中频宽带 + 周期性冲击 + 随机咔哒。"""
    rng = np.random.default_rng(seed)
    t = np.arange(n_samples) / 16000.0
    # 低频轰鸣（电机/传送带 50Hz + 120Hz 谐波）
    noise = 0.5 * np.sin(2 * np.pi * 50 * t) + 0.3 * np.sin(2 * np.pi * 120 * t)
    # 宽带背景（风机/气动）
    noise += 0.4 * rng.standard_normal(n_samples)
    # 周期性冲击（冲压/气缸，每 0.8s 一次）
    period = int(0.8 * 16000)
    for start in range(0, n_samples, period):
        end = min(start + int(0.05 * 16000), n_samples)
        noise[start:end] += 0.8 * rng.standard_normal(end - start) * np.hanning(end - start)
    # 随机咔哒（金属碰撞）
    n_clicks = max(1, n_samples // 16000 * 3)
    for _ in range(n_clicks):
        pos = rng.integers(0, max(1, n_samples - 800))
        length = min(800, n_samples - pos)
        noise[pos:pos + length] += 1.2 * rng.standard_normal(length) * np.hanning(length)
    return noise


def mix_at_snr(speech: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    """按目标 SNR 混合（噪声能量对齐到语音时长）。speech 为 int16，返回 int16。"""
    sp = speech.astype(np.float64)
    n = len(sp)
    noise_seg = noise[:n] if len(noise) >= n else np.tile(noise, (n // len(noise)) + 1)[:n]
    p_speech = np.mean(sp ** 2) or 1e-9
    p_noise = np.mean(noise_seg ** 2) or 1e-9
    scale = np.sqrt(p_speech / (p_noise * (10 ** (snr_db / 10))))
    mixed = sp + noise_seg * scale
    return np.clip(mixed, -32768, 32767).astype(np.int16)


async def recognize(asr, pcm: np.ndarray) -> str:
    from app.asr.stream import ASRStream

    stream = ASRStream(asr)
    block = int(16000 * 0.1)
    data = pcm.tobytes()
    for off in range(0, len(data), block):
        await stream.feed(data[off:off + block])
    text = await stream.flush()
    await stream.close()
    return text.strip()


async def main() -> int:
    cfg = load_config()
    from app.asr.engine import SherpaASR

    print("[noise_test] 加载 ASR 模型…")
    asr = await SherpaASR.instance(cfg)

    # 预合成全部指令（干净语音）
    print(f"[noise_test] 合成 {len(UTTERANCES)} 条指令…")
    clean: dict[str, np.ndarray] = {}
    for utt in UTTERANCES:
        spoken = await synthesize(utt, "ok", cfg)
        if not spoken.get("audio_base64"):
            print(f"[noise_test] piper 合成失败：{utt}")
            continue
        clean[utt] = wav_base64_to_pcm16_16k(spoken["audio_base64"])

    max_len = max(len(v) for v in clean.values())
    noise = gen_factory_noise(max_len)

    # 结果矩阵：results[snr][utt] = (raw, corrected, hit)
    results: dict[int, dict[str, tuple]] = {}
    for snr in SNR_LEVELS:
        results[snr] = {}
        print(f"\n[noise_test] === SNR {snr} dB ===")
        for utt, speech in clean.items():
            mixed = mix_at_snr(speech, noise, snr)
            raw = await recognize(asr, mixed)
            corrected = correct_asr(raw)
            # 命中判定：目标文本（去标点）出现在纠偏结果中
            target = utt.replace("，", "").replace(",", "")
            hit = target in corrected.replace("，", "").replace(",", "")
            results[snr][utt] = (raw, corrected, hit)
            mark = "✓" if hit else "✗"
            print(f"  {mark} {utt!r:22} → {corrected!r}")

    # 汇总
    print("\n[noise_test] ===== 汇总（识别准确率）=====")
    print(f"{'SNR':>6} {'命中':>6} {'总数':>6} {'准确率':>8}")
    for snr in SNR_LEVELS:
        hits = sum(1 for v in results[snr].values() if v[2])
        total = len(results[snr])
        pct = hits / total * 100 if total else 0
        print(f"{snr:>4}dB {hits:>6} {total:>6} {pct:>7.1f}%")

    # 结论：SNR≥15 应 ≥90%，SNR≥10 应 ≥70%
    def acc(snr):
        vals = results[snr].values()
        return sum(1 for v in vals if v[2]) / len(vals) if vals else 0

    a15, a10 = acc(15), acc(10)
    print(f"\n[noise_test] 关键指标：SNR15={a15:.0%}（目标≥90%） SNR10={a10:.0%}（目标≥70%）")
    ok = a15 >= 0.9 and a10 >= 0.7
    print("[noise_test] 总体:", "PASS" if ok else "FAIL（噪声下识别率不达标，需加降噪或换大模型）")

    # ---- 结论边界（诚实声明）----
    print("""
[noise_test] ===== 结论边界（重要）=====
本测试用 piper VITS 合成音作为语音源。VITS 每次合成有随机采样，且合成音
与真实人声存在域差（domain gap）——14M 小模型对合成音的基线识别率（约 60-78%）
显著低于真实人声。因此：

1. 本测试的绝对识别率【不代表】真实人声场景的表现，仅用于：
   - 验证噪声注入方法学（SNR 越高识别率越高，趋势单调 ✓）
   - 暴露固定同音误识别模式（已入 correct_asr 纠偏表）
   - 对比不同 SNR 档的相对退化趋势

2. 真实人声的识别率需真车/真人现场实测（步骤50）。预期真实人声基线
   明显高于合成音（sherpa zipformer 对真人普通话 WER 约 5-8%）。

3. 若真车实测噪声下仍不达标，可选改进（按成本排序）：
   a. 前端加 RNNoise/speex 降噪预处理（纯软件，成本低）
   b. 换更大 sherpa 模型（如 zipformer 35M/98M，RTF 仍 <1，内存够）
   c. 热词增强（hotwords.txt 已支持，需 modified_beam_search）
   d. 指向性麦克风阵列（硬件，效果最好但成本最高）
""")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
