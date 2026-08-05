"""SherpaASR：sherpa-onnx 流式中文识别引擎（单例懒加载）。

- 模型目录从 config asr 段读取；encoder/decoder/joiner 的 int8 onnx 按 glob 查找，
  兼容文件名中 epoch/chunk 等差异；tokens.txt 必须在模型目录根。
- 识别推理为 CPU 密集操作，统一经模块级 ThreadPoolExecutor 执行，不阻塞事件循环。
- sherpa-onnx 未安装或模型缺失时 available=False，调用方降级（不崩溃）。
"""
import asyncio
import glob
import os
from concurrent.futures import ThreadPoolExecutor

from ..config import CORE_ROOT

# ASR 推理线程池（zipformer 单句解码约几十~几百 ms）
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="asr")

try:
    import numpy as np
    import sherpa_onnx

    _IMPORT_ERROR = None
except Exception as e:  # pragma: no cover - 环境缺依赖时降级
    np = None
    sherpa_onnx = None
    _IMPORT_ERROR = e


class SherpaStream:
    """单条识别流（同步 API；由 ASRStream 经 executor 调度）。"""

    def __init__(self, recognizer, sample_rate: int):
        self._recognizer = recognizer
        self._stream = recognizer.create_stream()
        self._sample_rate = sample_rate

    def accept_pcm16(self, pcm: bytes) -> None:
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        self._stream.accept_waveform(self._sample_rate, samples)

    def input_finished(self) -> None:
        self._stream.input_finished()

    def is_ready(self) -> bool:
        return self._recognizer.is_ready(self._stream)

    def decode(self) -> None:
        self._recognizer.decode_stream(self._stream)

    def get_text(self) -> str:
        return self._recognizer.get_result(self._stream)

    def is_endpoint(self) -> bool:
        return self._recognizer.is_endpoint(self._stream)

    def reset(self) -> None:
        self._recognizer.reset(self._stream)


class SherpaASR:
    _instance: "SherpaASR | None" = None
    _lock = asyncio.Lock()

    def __init__(self, cfg: dict):
        if sherpa_onnx is None:
            raise RuntimeError(f"sherpa-onnx 不可用: {_IMPORT_ERROR}")
        asr_cfg = cfg.get("asr", {})
        model_dir = asr_cfg.get(
            "model_dir", "models/asr/sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23"
        )
        if not os.path.isabs(model_dir):
            model_dir = str(CORE_ROOT / model_dir)

        def find(pattern: str) -> str:
            matches = sorted(glob.glob(os.path.join(model_dir, pattern)))
            if not matches:
                raise FileNotFoundError(f"ASR 模型文件缺失: {model_dir}/{pattern}")
            return matches[0]

        self._sample_rate = int(asr_cfg.get("sample_rate", 16000))
        self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            encoder=find("encoder*.int8.onnx"),
            decoder=find("decoder*.int8.onnx"),
            joiner=find("joiner*.int8.onnx"),
            tokens=find("tokens.txt"),
            num_threads=int(asr_cfg.get("num_threads", 2)),
            sample_rate=self._sample_rate,
            feature_dim=80,
            enable_endpoint_detection=bool(asr_cfg.get("enable_endpoint", True)),
            rule1_min_trailing_silence=float(
                asr_cfg.get("rule1_min_trailing_silence", 2.4)
            ),
            rule2_min_trailing_silence=float(
                asr_cfg.get("rule2_min_trailing_silence", 1.2)
            ),
            rule3_min_utterance_length=float(
                asr_cfg.get("rule3_min_utterance_length", 20.0)
            ),
            decoding_method=asr_cfg.get("decoding_method", "modified_beam_search"),
            max_active_paths=int(asr_cfg.get("max_active_paths", 4)),
            provider="cpu",
            hotwords_file=self._find_hotwords(model_dir, asr_cfg),
            hotwords_score=float(asr_cfg.get("hotwords_score", 2.0)),
        )

    @staticmethod
    def _find_hotwords(model_dir: str, asr_cfg: dict) -> str:
        """热词文件：config 指定或模型目录上级 asr/hotwords.txt；不存在则空。"""
        p = asr_cfg.get("hotwords_file")
        if not p:
            p = os.path.join(os.path.dirname(model_dir.rstrip("/")), "hotwords.txt")
        elif not os.path.isabs(p):
            p = str(CORE_ROOT / p)
        return p if os.path.isfile(p) else ""

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @classmethod
    async def instance(cls, cfg: dict) -> "SherpaASR":
        """单例懒加载；模型加载放线程池，避免阻塞事件循环。"""
        async with cls._lock:
            if cls._instance is None:
                loop = asyncio.get_running_loop()
                cls._instance = await loop.run_in_executor(_executor, cls, cfg)
            return cls._instance

    @classmethod
    def loaded(cls) -> bool:
        return cls._instance is not None

    def create_stream(self) -> SherpaStream:
        return SherpaStream(self._recognizer, self._sample_rate)


def run_in_asr_executor(fn, *args):
    """把同步 CPU 密集推理调度到 ASR 线程池。"""
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(_executor, fn, *args)
