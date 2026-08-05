"""现场锁路由（对齐 index.ts /api/site*）。"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from .deps import auth, read_body

router = APIRouter()


@router.get("/api/site")
async def get_site(request: Request, _client_id: str = Depends(auth)):
    return request.app.state.sessions.get_site_public()


@router.post("/api/site/unlock")
async def site_unlock(request: Request, client_id: str = Depends(auth)):
    body = await read_body(request)
    sessions = request.app.state.sessions
    result = sessions.unlock_site(
        client_id,
        nonce=body.get("nonce"),
        code=body.get("code"),
        force=bool(body.get("force")),
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
    ok = request.app.state.sessions.end_site(client_id, bool(body.get("force")))
    if not ok:
        return JSONResponse(status_code=403, content={"succeed": False, "error": "not_holder"})
    request.app.state.bus.broadcast("site_changed", {"holderClientId": None})
    return {"succeed": True}
