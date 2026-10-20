"""现场锁路由（对齐 index.ts /api/site*）。"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from .deps import auth, read_body

router = APIRouter()


def _force_allowed(request: Request, client_id: str) -> bool:
    sessions = request.app.state.sessions
    if sessions.is_local(client_id):
        return True
    return sessions.remote_force_allowed()


@router.get("/api/site")
async def get_site(request: Request, _client_id: str = Depends(auth)):
    return request.app.state.sessions.get_site_public()


@router.post("/api/site/unlock")
async def site_unlock(request: Request, client_id: str = Depends(auth)):
    body = await read_body(request)
    sessions = request.app.state.sessions
    local = bool(body.get("local"))
    if local and not sessions.is_local(client_id):
        return JSONResponse(status_code=403, content={"succeed": False, "error": "not_local"})
    force = bool(body.get("force")) and _force_allowed(request, client_id)
    result = sessions.unlock_site(
        client_id,
        nonce=body.get("nonce"),
        code=body.get("code"),
        force=force,
        bypass_code=local,
    )
    if not result["ok"]:
        if result["error"] == "held":
            return JSONResponse(
                status_code=409,
                content={
                    "succeed": False,
                    "error": "held",
                    "holderClientId": result["holderClientId"],
                },
            )
        return JSONResponse(status_code=400, content={"succeed": False, "error": "invalid"})
    session = result["session"]
    request.app.state.bus.broadcast(
        "site_changed",
        {
            "holderClientId": session["holderClientId"],
            "expiresAt": session["expiresAt"],
            "stolen": bool(result.get("stolen")),
        },
    )
    return {
        "succeed": True,
        "siteSessionId": session["siteSessionId"],
        "expiresAt": session["expiresAt"],
        "holderClientId": session["holderClientId"],
    }


@router.post("/api/site/end")
async def site_end(request: Request, client_id: str = Depends(auth)):
    body = await read_body(request)
    force = bool(body.get("force")) and _force_allowed(request, client_id)
    ok = request.app.state.sessions.end_site(client_id, force)
    if not ok:
        return JSONResponse(status_code=403, content={"succeed": False, "error": "not_holder"})
    request.app.state.bus.broadcast("site_changed", {"holderClientId": None})
    return {"succeed": True}
