"""配对路由（对齐 index.ts /api/pair/*）。"""
import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..console.net import resolve_public_host
from .deps import read_body, require_local

router = APIRouter()


def _loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost")


def _token_eq(a: str, b: str) -> bool:
    a = str(a or "")
    b = str(b or "")
    if len(a) != len(b) or not a:
        return False
    return secrets.compare_digest(a, b)


@router.post("/api/pair/start")
async def pair_start(request: Request):
    pending = request.app.state.sessions.start_pair()
    return {"code": pending["code"], "expiresAt": pending["expiresAt"]}


@router.get("/api/pair/pending")
async def pair_pending(request: Request):
    pending = request.app.state.sessions.get_pair_pending()
    return pending or {"code": None, "expiresAt": 0}


@router.post("/api/pair/confirm")
async def pair_confirm(request: Request):
    body = await read_body(request)
    code = str(body.get("code") or "")
    result = request.app.state.sessions.confirm_pair(code)
    if not result:
        return JSONResponse(status_code=400, content={"succeed": False, "error": "invalid_code"})
    return {
        "succeed": True,
        "pairToken": result["pairToken"],
        "expiresAt": result["expiresAt"],
        "clientId": result["clientId"],
        "vehicleId": request.app.state.cfg["vehicleId"],
    }


@router.post("/api/pair/local")
async def pair_local(request: Request):
    """车载屏免码配对：本机回环地址 + kiosk 口令。"""
    if not _loopback(request):
        return JSONResponse(status_code=403, content={"succeed": False, "error": "not_local"})
    body = await read_body(request)
    store = request.app.state.console_store
    if not _token_eq(str(body.get("kioskToken") or ""), store.kiosk_token()):
        return JSONResponse(status_code=403, content={"succeed": False, "error": "invalid_kiosk"})
    result = request.app.state.sessions.pair_local()
    return {
        "succeed": True,
        "pairToken": result["pairToken"],
        "expiresAt": result["expiresAt"],
        "clientId": result["clientId"],
        "vehicleId": request.app.state.cfg["vehicleId"],
        "local": True,
    }


@router.post("/api/pair/join")
async def pair_join(request: Request):
    """核销扫码凭证：配对并强制取得现场锁（视为车载屏授权的转交）。"""
    body = await read_body(request)
    sessions = request.app.state.sessions
    result = sessions.consume_join_ticket(str(body.get("ticket") or ""))
    if not result:
        return JSONResponse(status_code=400, content={"succeed": False, "error": "invalid_ticket"})
    unlocked = sessions.unlock_site(result["clientId"], force=True, bypass_code=True)
    session = unlocked["session"]
    request.app.state.bus.broadcast(
        "site_changed",
        {
            "holderClientId": session["holderClientId"],
            "expiresAt": session["expiresAt"],
            "stolen": bool(unlocked.get("stolen")),
        },
    )
    request.app.state.bus.broadcast(
        "join_consumed",
        {"holderClientId": session["holderClientId"]},
    )
    return {
        "succeed": True,
        "pairToken": result["pairToken"],
        "expiresAt": result["expiresAt"],
        "clientId": result["clientId"],
        "vehicleId": request.app.state.cfg["vehicleId"],
        "local": False,
        "siteSessionId": session["siteSessionId"],
        "siteExpiresAt": session["expiresAt"],
        "holderClientId": session["holderClientId"],
    }


@router.get("/api/pair/join-ticket")
async def join_ticket(request: Request, _client_id: str = Depends(require_local)):
    sessions = request.app.state.sessions
    issued = sessions.issue_join_ticket()
    cfg = request.app.state.cfg
    host = await resolve_public_host(cfg, request.app.state.jarvis)
    port = cfg["server"]["port"]
    vehicle = cfg["vehicleId"]
    url = f"http://{host}:{port}/?v={vehicle}&join={issued['ticket']}"
    return {"ticket": issued["ticket"], "expiresAt": issued["expiresAt"], "url": url}
