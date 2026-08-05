"""auth 依赖：Bearer token → SessionManager.resolve_token。

未配对抛 UnpairedError（main.py 注册异常处理器返回 401 {"succeed":false,"error":"unpaired"}）。
成功时把 client_id / pair_token 注入 request.state。
"""
from fastapi import Request


class UnpairedError(Exception):
    pass


async def read_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


async def auth(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    token = header[7:] if header.startswith("Bearer ") else ""
    rec = request.app.state.sessions.resolve_token(token)
    if not rec:
        raise UnpairedError()
    request.state.client_id = rec["clientId"]
    request.state.pair_token = token
    return rec["clientId"]
