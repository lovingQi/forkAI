"""配对路由（对齐 index.ts /api/pair/*）。"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .deps import read_body

router = APIRouter()


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
