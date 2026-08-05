"""车载常听采集（默认关闭）。

config cabin_listen.enabled=true 时启动：sounddevice 读 ALSA 麦（16kHz mono PCM16），
直接喂内部 ASRStream；final 文本走 run_utterance(channel="cabin")。
sounddevice 导入失败 / 无音频设备 / ASR 不可用：只打印告警，不影响服务启动。

注意：车载常听没有配对客户端上下文，clientId 取当前现场锁持有者（无持有者时用
"cabin" 占位）——cabin 通道运动意图本身要求持有现场锁，与 HTTP 入口行为一致。
"""
import asyncio

from ..asr.engine import SherpaASR
from ..asr.stream import ASRStream


class _AppShim:
    """run_utterance 只用到 request.app.state，给非 HTTP 入口一个最小壳。"""

    def __init__(self, app):
        self.app = app


class CabinListener:
    def __init__(self, app):
        self._app = app
        self._task: asyncio.Task | None = None
        self._sd_stream = None

    async def start(self) -> None:
        cfg = self._app.state.cfg.get("cabin_listen", {}) or {}
        if not cfg.get("enabled"):
            return
        try:
            import sounddevice as sd  # noqa: F401
        except Exception as e:
            print(f"[forkai-core] cabin_listen 已启用但 sounddevice 不可用，跳过: {e}")
            return
        self._task = asyncio.create_task(self._run(cfg))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        if self._sd_stream:
            try:
                self._sd_stream.stop()
                self._sd_stream.close()
            except Exception:
                pass
            self._sd_stream = None

    async def _run(self, cfg: dict) -> None:
        import sounddevice as sd

        try:
            asr = await SherpaASR.instance(self._app.state.cfg)
        except Exception as e:
            print(f"[forkai-core] cabin_listen ASR 初始化失败，跳过: {e}")
            return

        sample_rate = asr.sample_rate
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        def callback(indata, frames, time_info, status):  # noqa: ARG001
            def _enqueue():
                try:
                    queue.put_nowait(bytes(indata))
                except asyncio.QueueFull:
                    pass

            loop.call_soon_threadsafe(_enqueue)

        try:
            self._sd_stream = sd.RawInputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
                device=cfg.get("device"),
                blocksize=int(sample_rate * 0.1),  # 100ms 块
                callback=callback,
            )
            self._sd_stream.start()
        except Exception as e:
            print(f"[forkai-core] cabin_listen 打开音频设备失败，跳过: {e}")
            return

        print("[forkai-core] cabin_listen 车载常听已启动")
        stream = ASRStream(asr)
        shim = _AppShim(self._app)
        sessions = self._app.state.sessions
        bus = self._app.state.bus
        # 延迟 import 避免循环依赖
        from ..api.routes_voice import run_utterance

        try:
            while True:
                pcm = await queue.get()
                events = await stream.feed(pcm)
                for is_final, text in events:
                    if not is_final:
                        continue
                    text = text.strip()
                    if not text:
                        continue
                    client_id = sessions.get_site_holder() or "cabin"
                    bus.broadcast(
                        "asr_final",
                        {"clientId": client_id, "channel": "cabin", "text": text},
                    )
                    try:
                        await run_utterance(shim, client_id, "cabin", text)
                    except Exception as e:
                        print(f"[forkai-core] cabin_listen run_utterance 异常: {e}")
        except asyncio.CancelledError:
            pass
        finally:
            await stream.close()
