"""ASRStream：一路音频的高级封装。

- feed(pcm_bytes) → [(is_final, text), ...]：有新 partial 时出 (False, text)；
  检测到 endpoint 时出 (True, final_text) 并自动 reset 继续听下一句。
- flush()：手动结束当前句（PTT 松开），强制解码残余音频出 final。
- close()：取残余文本后释放（不再 reset）。
partial 文本用于前端实时显示。
"""
from .engine import SherpaASR, run_in_asr_executor


class ASRStream:
    def __init__(self, asr: SherpaASR):
        self._stream = asr.create_stream()
        self._last_partial = ""
        self._closed = False

    async def feed(self, pcm: bytes) -> list:
        if self._closed:
            return []
        return await run_in_asr_executor(self._feed_sync, pcm)

    def _feed_sync(self, pcm: bytes) -> list:
        events = []
        self._stream.accept_pcm16(pcm)
        while self._stream.is_ready():
            self._stream.decode()
        text = self._stream.get_text()
        if text and text != self._last_partial:
            self._last_partial = text
            events.append((False, text))
        if self._stream.is_endpoint():
            final = self._stream.get_text() or self._last_partial
            self._stream.reset()
            self._last_partial = ""
            if final:
                events.append((True, final))
        return events

    async def flush(self) -> str:
        """手动 flush：标记输入结束，解码残余，取全文并重置流。"""
        if self._closed:
            return ""
        return await run_in_asr_executor(self._flush_sync)

    def _flush_sync(self) -> str:
        # 尾 padding：流式模型需要一小段静音才会吐出末尾 token
        self._stream.accept_pcm16(b"\x00" * int(16000 * 2 * 0.6))
        self._stream.input_finished()
        while self._stream.is_ready():
            self._stream.decode()
        final = self._stream.get_text() or self._last_partial
        self._stream.reset()
        self._last_partial = ""
        return final

    async def close(self) -> str:
        """取残余文本（不重置）；之后 feed 不再生效。"""
        if self._closed:
            return ""
        self._closed = True
        return await run_in_asr_executor(self._residual_sync)

    def _residual_sync(self) -> str:
        self._stream.input_finished()
        while self._stream.is_ready():
            self._stream.decode()
        return self._stream.get_text() or self._last_partial
