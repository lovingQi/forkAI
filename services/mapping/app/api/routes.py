"""产品 REST。只监听本机，鉴权由 forkai-core 负责。"""
import uuid
from datetime import datetime, timezone
from pathlib import Path

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
    draft = parse_draft(await request.json(), ctx.status()["capabilities"])
    return await run_preflight(draft, ctx.cfg, ctx.profiles, ctx.urdf, ctx.scheduler, ctx.recordings)


@router.post("/tasks")
async def start_task(request: Request):
    ctx = _ctx(request)
    draft = parse_draft(await request.json(), ctx.status()["capabilities"])
    task = MappingTask(
        id=str(uuid.uuid4()),
        dimension=draft["dimension"],
        mode=draft["mode"],
        name=draft["name"],
        profileId=draft["profileId"],
        profileRevision="",
        inputs=draft["inputs"],
        source=draft["source"],
        objective=draft["objective"],
        createdAt=datetime.now(timezone.utc).isoformat(),
    )
    for profile in ctx.profiles:
        if profile["id"] == draft["profileId"]:
            task.profileRevision = profile["runtimeRevision"]
    await ctx.scheduler.acquire(task.id)
    ctx.tasks.save(task)
    ctx.live.open(task.id)
    if draft["dimension"] == "3d":
        from ..session.traverse_session import TraverseSession
        session = TraverseSession(ctx, task)
    else:
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


@router.post("/tasks/{task_id}/pause")
async def pause(task_id: str, request: Request):
    return _command(_ctx(request), task_id, "pause", 200)


@router.post("/tasks/{task_id}/resume")
async def resume(task_id: str, request: Request):
    return _command(_ctx(request), task_id, "resume", 200)


@router.post("/tasks/{task_id}/confirm-alignment")
async def confirm_alignment(task_id: str, request: Request):
    return _command(_ctx(request), task_id, "confirm", 200)


@router.get("/recordings")
async def recordings(request: Request):
    return {"recordings": _ctx(request).recordings.list()}


@router.post("/recordings")
async def start_recording(request: Request):
    import asyncio
    ctx = _ctx(request)
    body = await request.json()
    name = str(body.get("name") or "").strip()
    topics = body.get("topics") or []
    if not name or not isinstance(topics, list) or not topics:
        raise MappingError("MAPPING_INPUT_INVALID", "录包需要名称和话题")
    await ctx.scheduler.acquire(f"recording-{name}")
    meta = ctx.recordings.create(name, body.get("dimension") or "2d", "self", [{"topic": t["topic"] if isinstance(t, dict) else t, "schema": (t.get("schema") if isinstance(t, dict) else "")} for t in topics])
    fox = ctx.cfg["foxglove"]
    limit = int((ctx.cfg.get("recordings") or {}).get("maxBytes") or 20 * 1024 ** 3)
    from ..recording.recorder import BridgeRecorder
    recorder = BridgeRecorder(meta, fox["url"], limit, int(ctx.cfg.get("minFreeBytes") or 0), int(fox.get("maxMessageBytes2d") or 2 * 1024 * 1024))
    ctx.recorder = recorder

    async def _run():
        try:
            saved = await recorder.run()
            ctx.recordings.save(saved)
        finally:
            await ctx.scheduler.release(f"recording-{name}")
            ctx.recorder = None

    asyncio.create_task(_run())
    return JSONResponse(status_code=201, content=meta)


@router.post("/recordings/{rec_id}/stop")
async def stop_recording(rec_id: str, request: Request):
    ctx = _ctx(request)
    if ctx.recorder is None or ctx.recorder.meta.get("id") != rec_id:
        raise MappingError("MAPPING_INPUT_INVALID", "没有正在进行的录包", status=404)
    ctx.recorder.stop("stopped")
    return ctx.recorder.meta


@router.delete("/recordings/{rec_id}")
async def delete_recording(rec_id: str, request: Request):
    try:
        _ctx(request).recordings.delete(rec_id)
    except FileNotFoundError:
        raise MappingError("MAPPING_INPUT_INVALID", "录包不存在", status=404)
    return Response(status_code=204)


@router.post("/recordings/import")
async def import_recording(request: Request):
    """只给 core 调用。路径必须落在配置的 jarvis 录包目录里。"""
    ctx = _ctx(request)
    body = await request.json()
    raw = str(body.get("path") or "")
    root = Path(str((ctx.cfg.get("recordings") or {}).get("jarvisImportDir") or ""))
    if not root:
        raise MappingError("MAPPING_INPUT_INVALID", "没有配置 jarvis 录包目录")
    path = Path(raw).resolve()
    if root.resolve() not in path.parents and path != root.resolve():
        raise MappingError("MAPPING_INPUT_INVALID", "录包路径不在允许的目录内")
    meta = ctx.recordings.create(str(body.get("name") or path.stem), "2d", "jarvis", [])
    target = Path(meta["path"])
    target.write_bytes(path.read_bytes())
    meta["status"] = "ready"
    meta["bytes"] = target.stat().st_size
    ctx.recordings.save(meta)
    return meta


def _command(ctx, task_id: str, action: str, status_code: int):
    task = _task_or_404(ctx, task_id)
    if task.status in TERMINAL:
        raise MappingError("MAPPING_TASK_INTERRUPTED", "任务已经结束")
    if action == "finish" and task.status not in {"running", "ready-to-save", "paused"}:
        raise MappingError("MAPPING_INPUT_INVALID", "当前状态不能保存")
    if action == "pause" and task.status != "running":
        raise MappingError("MAPPING_INPUT_INVALID", "当前状态不能暂停")
    if action == "resume" and task.status != "paused":
        raise MappingError("MAPPING_INPUT_INVALID", "当前状态不能继续")
    if action == "confirm" and task.alignmentPhase != "awaiting-confirmation":
        raise MappingError("MAPPING_INPUT_INVALID", "当前没有待确认的对齐")
    if action == "finish" and task.alignmentPhase == "awaiting-confirmation":
        raise MappingError("MAPPING_INPUT_INVALID", "先确认对齐再保存")
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
