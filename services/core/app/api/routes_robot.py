"""车端读代理与控制路由（对齐 index.ts /api/state|map|params|control/:action）。

- GET 代理失败返回 502 {"error": msg}
- POST /api/control/drive 需持有现场锁，否则 403 no_site
- POST /api/control/stop 走 watchdog.stop
- 其余 action 转发 jarvis.control，网络异常 502
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..jarvis.client import JarvisError
from .deps import auth, read_body

router = APIRouter()


async def _proxy_get(request: Request, method: str):
    try:
        return await getattr(request.app.state.jarvis, method)()
    except JarvisError as e:
        return JSONResponse(status_code=502, content={"error": str(e)})


@router.get("/api/state")
async def get_state(request: Request, _client_id: str = Depends(auth)):
    return await _proxy_get(request, "get_state")


@router.get("/api/map")
async def get_map(request: Request, _client_id: str = Depends(auth)):
    return await _proxy_get(request, "get_map")


@router.get("/api/params")
async def get_params(request: Request, _client_id: str = Depends(auth)):
    return await _proxy_get(request, "get_params")


@router.post("/api/control/{action}")
async def control(action: str, request: Request, client_id: str = Depends(auth)):
    # drive requires site
    if action == "drive":
        if not request.app.state.sessions.has_site(client_id):
            return JSONResponse(status_code=403, content={"succeed": False, "error": "no_site"})
    if action == "stop":
        await request.app.state.watchdog.stop(False)
        return {"succeed": True}
    try:
        body = await read_body(request)
        return await request.app.state.jarvis.control(action, body)
    except JarvisError as e:
        return JSONResponse(status_code=502, content={"succeed": False, "error": str(e)})
