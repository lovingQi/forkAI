"""通过 jarvis TCP 开始或停止扫图录包。真车目录核对点 G 未关闭。"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..jarvis.tcp_client import JarvisTcpClient
from .deps import auth, read_body

router = APIRouter()


def _client(cfg: dict) -> JarvisTcpClient:
    tcp = (cfg.get("jarvis") or {}).get("tcp") or {}
    return JarvisTcpClient(
        str(tcp.get("host") or "127.0.0.1"),
        int(tcp.get("port") or 7273),
        str(tcp.get("user") or "test"),
        str(tcp.get("password") or "test"),
    )


@router.post("/api/mapping-jarvis/start")
async def start(request: Request, client_id: str = Depends(auth)):
    if not request.app.state.sessions.has_site(client_id):
        return JSONResponse(status_code=403, content={"succeed": False, "error": "no_site"})
    body = await read_body(request)
    name = str(body.get("name") or "").strip()
    if not name:
        return JSONResponse(status_code=400, content={"succeed": False, "error": "invalid"})
    try:
        reply = await _client(request.app.state.cfg).command("UmMapping", {"name": name, "flag": True})
    except Exception as exc:
        return JSONResponse(status_code=502, content={"succeed": False, "error": str(exc)})
    return {"succeed": True, "reply": reply}


@router.post("/api/mapping-jarvis/stop")
async def stop(request: Request, client_id: str = Depends(auth)):
    if not request.app.state.sessions.has_site(client_id):
        return JSONResponse(status_code=403, content={"succeed": False, "error": "no_site"})
    try:
        reply = await _client(request.app.state.cfg).command("UmMapping", {"name": "", "flag": False})
    except Exception as exc:
        return JSONResponse(status_code=502, content={"succeed": False, "error": str(exc)})
    return {"succeed": True, "reply": reply}


@router.get("/api/mapping-jarvis/state")
async def state(request: Request, _client_id: str = Depends(auth)):
    try:
        reply = await _client(request.app.state.cfg).command("UmGetMappingState")
    except Exception as exc:
        return JSONResponse(status_code=502, content={"succeed": False, "error": str(exc)})
    return {"succeed": True, "reply": reply}
