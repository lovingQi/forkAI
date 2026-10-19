"""GET /api/trace：最近追踪日志与当前任务（需配对）。"""
from fastapi import APIRouter, Depends, Request

from .deps import auth

router = APIRouter()


@router.get("/api/trace")
async def get_trace(request: Request, _client_id: str = Depends(auth)):
    tracer = request.app.state.tracer
    raw = request.query_params.get("limit")
    try:
        limit = int(raw) if raw not in (None, "") else 300
    except (TypeError, ValueError):
        limit = 300
    limit = max(1, min(limit, tracer.capacity))
    return {"entries": tracer.history(limit) or [], "mission": tracer.current_mission()}
