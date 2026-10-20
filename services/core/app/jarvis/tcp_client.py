"""jarvis TCP 建图录包。帧：#$<十进制长度>##<JSON>~$，命令键是 #CMD#。

登录字段按 mock 与反汇编里的 user/password。真车字段核对点 G 未关闭。
"""
import asyncio
import json


def _frame(payload: dict) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"#${len(body)}##".encode("utf-8") + body + b"~$"


async def _read_frame(reader: asyncio.StreamReader) -> dict:
    header = await reader.readuntil(b"##")
    if not header.startswith(b"#$"):
        raise RuntimeError("jarvis 帧头不对")
    length = int(header[2:-2])
    body = await reader.readexactly(length)
    tail = await reader.readexactly(2)
    if tail != b"~$":
        raise RuntimeError("jarvis 帧尾不对")
    return json.loads(body.decode("utf-8"))


class JarvisTcpClient:
    def __init__(self, host: str, port: int, user: str, password: str):
        self.host = host
        self.port = port
        self.user = user
        self.password = password

    async def command(self, cmd: str, fields: dict | None = None) -> dict:
        reader, writer = await asyncio.open_connection(self.host, self.port)
        try:
            login = await self._send(reader, writer, "UmConnect", {"user": self.user, "password": self.password})
            if not login.get("ok", True):
                raise RuntimeError("jarvis 登录失败")
            return await self._send(reader, writer, cmd, fields or {})
        finally:
            writer.close()
            await writer.wait_closed()

    async def _send(self, reader, writer, cmd: str, fields: dict) -> dict:
        writer.write(_frame({"#CMD#": cmd, **fields}))
        await writer.drain()
        return await _read_frame(reader)
