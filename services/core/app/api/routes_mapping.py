"""把 /api/mapping 代理到本机建图服务。写操作需要现场锁。"""
import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from .deps import auth

router = APIRouter()


def _needs_site(method: str, path: str) -> bool:
    if method == "POST" and path == "tasks":
        return True
    if method == "POST" and path.startswith("tasks/") and path.rsplit("/", 1)[-1] in {"finish", "cancel"}:
        return True
    if method == "DELETE" and path.startswith("maps/"):
        return True
    return False


@router.api_route("/api/mapping/{path:path}", methods=["GET", "POST", "DELETE"])
async def proxy_mapping(path: str, request: Request, client_id: str = Depends(auth)):
    if _needs_site(request.method, path) and not request.app.state.sessions.has_site(client_id):
        return JSONResponse(status_code=403, content={"succeed": False, "error": "no_site"})
    mapping = request.app.state.cfg.get("mapping") or {}
    base = str(mapping.get("baseUrl") or "").rstrip("/")
    client: httpx.AsyncClient | None = getattr(request.app.state, "mapping_http", None)
    if not base or client is None:
        return JSONResponse(status_code=502, content={"succeed": False, "error": "mapping_unavailable"})
    url = f"{base}/api/v1/mapping/{path}"
    body = await request.body()
    headers = {"X-ForkAI-Client-Id": client_id}
    if request.headers.get("content-type"):
        headers["content-type"] = request.headers["content-type"]
    try:
        upstream = await client.request(request.method, url, params=request.query_params, content=body, headers=headers)
    except httpx.HTTPError:
        return JSONResponse(status_code=502, content={"succeed": False, "error": "mapping_unavailable"})
    media = upstream.headers.get("content-type", "application/json")
    return Response(content=upstream.content, status_code=upstream.status_code, media_type=media.split(";")[0])
