"""拉起引擎进程：就绪探测 20 秒，SIGTERM 等 5 秒，再 SIGKILL 等 5 秒。"""
import asyncio
import os
import signal
from pathlib import Path

from ..errors import MappingError


class EngineProcess:
    def __init__(self, executable: str, arguments: list[str], cwd: str, env: dict, log_path: Path,
                 ready_timeout: float, stop_timeout: float, kill_timeout: float, endpoint: str):
        self.executable = executable
        self.arguments = arguments
        self.cwd = cwd
        self.env = env
        self.log_path = log_path
        self.ready_timeout = ready_timeout
        self.stop_timeout = stop_timeout
        self.kill_timeout = kill_timeout
        self.endpoint = endpoint
        self.proc: asyncio.subprocess.Process | None = None
        self._log = None

    async def start(self) -> None:
        if not os.path.isfile(self.executable) or not os.access(self.executable, os.X_OK):
            raise MappingError("MAPPING_ENGINE_UNAVAILABLE", "引擎程序不存在或不可执行")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(self.log_path, "ab")
        env = os.environ.copy()
        env.update(self.env)
        env["FORKAI_FAKE_ENGINE_ENDPOINT"] = self.endpoint
        self.proc = await asyncio.create_subprocess_exec(
            self.executable,
            *self.arguments,
            cwd=self.cwd,
            env=env,
            stdout=self._log,
            stderr=self._log,
            start_new_session=True,
        )
        await self._wait_ready()

    async def _wait_ready(self) -> None:
        import grpc

        channel = grpc.aio.insecure_channel(self.endpoint, options=[
            ("grpc.max_send_message_length", 64 * 1024 * 1024),
            ("grpc.max_receive_message_length", 64 * 1024 * 1024),
        ])
        try:
            await asyncio.wait_for(channel.channel_ready(), self.ready_timeout)
        except Exception as exc:
            await self.stop()
            raise MappingError("MAPPING_ENGINE_UNAVAILABLE", "引擎在时限内没有就绪") from exc
        finally:
            await channel.close()

    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def wait(self) -> int:
        if self.proc is None:
            return -1
        return await self.proc.wait()

    async def stop(self) -> None:
        proc = self.proc
        if proc is None or proc.returncode is not None:
            self._close_log()
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            self._close_log()
            return
        try:
            await asyncio.wait_for(proc.wait(), self.stop_timeout)
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), self.kill_timeout)
            except asyncio.TimeoutError:
                pass
        self._close_log()

    def _close_log(self) -> None:
        if self._log and not self._log.closed:
            self._log.close()
