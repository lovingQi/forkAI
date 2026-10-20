"""产品 REST。只监听本机，鉴权由 forkai-core 负责。"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from ..domain.draft import parse_draft
from ..domain.task import TERMINAL, MappingTask
from ..errors import MappingError
from ..ingest.preflight import run_preflight
from ..session.cartographer_online import OnlineSession

router = APIRouter(prefix="/api/v1/mapping")


def _ctx(request: Request):
    return request.app.state.ctx


def _task_or_404(ctx, task_id: str) -> MappingTask:
    task = ctx.tasks.load(task_id)
    if task is None:
        raise MappingError("MAPPING_INPUT_INVALID", "任务不存在", status=404)
    return task


@router.get("/status")
async def status(request: Request):
    return _ctx(request).status()


@router.get("/profiles")
async def profiles(request: Request):
    return {"profiles": _ctx(request).profiles}


@router.get("/robot")
async def robot(request: Request):
    model = _ctx(request).urdf
    return {"urdfLoaded": bool(model.get("links")), "urdfSha256": model.get("sha256"), "frames": sorted(model.get("links") or [])}


@router.post("/drafts/validate")
async def validate(request: Request):
    ctx = _ctx(request)
    draft = parse_draft(await request.json())
    return await run_preflight(draft, ctx.cfg, ctx.profiles, ctx.urdf, ctx.scheduler)


@router.post("/tasks")
async def start_task(request: Request):
    ctx = _ctx(request)
    draft = parse_draft(await request.json())
    task = MappingTask(
        id=str(uuid.uuid4()),
        dimension="2d",
        mode="online",
        name=draft["name"],
        profileId=draft["profileId"],
        profileRevision="",
        inputs=draft["inputs"],
        createdAt=datetime.now(timezone.utc).isoformat(),
    )
    for profile in ctx.profiles:
        if profile["id"] == draft["profileId"]:
            task.profileRevision = profile["runtimeRevision"]
    await ctx.scheduler.acquire(task.id)
    ctx.tasks.save(task)
    ctx.live.open(task.id)
    session = OnlineSession(ctx, task)
    ctx.sessions[task.id] = session
    import asyncio
    asyncio.create_task(session.run())
    return JSONResponse(status_code=201, content=task.to_dict())


@router.get("/tasks/active")
async def active(request: Request):
    ctx = _ctx(request)
    task_id = ctx.scheduler.active_task_id
    if not task_id:
        return Response(status_code=204)
    task = ctx.tasks.load(task_id)
    if task is None:
        return Response(status_code=204)
    return task.to_dict()


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, request: Request):
    return _task_or_404(_ctx(request), task_id).to_dict()


@router.get("/tasks/{task_id}/activity")
async def activity(task_id: str, request: Request, after: int = 0):
    _task_or_404(_ctx(request), task_id)
    return _ctx(request).activity(task_id).read(after)


@router.post("/tasks/{task_id}/finish")
async def finish(task_id: str, request: Request):
    return _command(_ctx(request), task_id, "finish", 202)


@router.post("/tasks/{task_id}/cancel")
async def cancel(task_id: str, request: Request):
    return _command(_ctx(request), task_id, "cancel", 200)


def _command(ctx, task_id: str, action: str, status_code: int):
    task = _task_or_404(ctx, task_id)
    if task.status in TERMINAL:
        raise MappingError("MAPPING_TASK_INTERRUPTED", "任务已经结束")
    if action == "finish" and task.status not in {"running", "ready-to-save"}:
        raise MappingError("MAPPING_INPUT_INVALID", "当前状态不能保存")
    session = ctx.sessions.get(task_id)
    if session is None:
        raise MappingError("MAPPING_TASK_INTERRUPTED", "任务不在本进程中")
    session.request(action)
    return JSONResponse(status_code=status_code, content=task.to_dict())


@router.get("/maps")
async def maps(request: Request):
    return {"maps": _ctx(request).maps.list_maps()}


@router.get("/maps/{project_id}")
async def map_detail(project_id: str, request: Request):
    return _ctx(request).maps.get(project_id)


@router.get("/maps/{project_id}/preview.png")
async def map_preview(project_id: str, request: Request):
    return Response(content=_ctx(request).maps.preview_png(project_id), media_type="image/png")


@router.get("/maps/{project_id}/export.zip")
async def map_export(project_id: str, request: Request):
    from ..assets.zip_export import export_zip
    blob = export_zip(_ctx(request).maps.directory(project_id))
    return Response(content=blob, media_type="application/zip")


@router.delete("/maps/{project_id}")
async def map_delete(project_id: str, request: Request):
    _ctx(request).maps.delete(project_id)
    return Response(status_code=204)
