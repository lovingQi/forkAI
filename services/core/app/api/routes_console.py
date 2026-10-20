"""控制台 REST。"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..console.controller import OpResult
from ..console.selfcheck import run_selfcheck
from .deps import auth, read_body

router = APIRouter()


def _respond(result: OpResult):
    body = {"succeed": True, **result.data} if result.ok else {"succeed": False, "error": result.error, **result.data}
    if result.ok:
        return body
    return JSONResponse(status_code=result.status, content=body)


def _service_result(result: dict):
    if result.get("ok"):
        payload = {"succeed": True}
        payload.update({k: v for k, v in result.items() if k not in ("ok", "status", "error")})
        return payload
    body = {"succeed": False, "error": result.get("error")}
    if result.get("details") is not None:
        body["details"] = result["details"]
    if result.get("backupId"):
        body["backupId"] = result["backupId"]
    return JSONResponse(status_code=int(result.get("status") or 400), content=body)


@router.get("/api/console/selfcheck")
async def selfcheck(request: Request):
    return await run_selfcheck(request.app.state.jarvis)


@router.get("/api/console/setup")
async def get_setup(request: Request, _client_id: str = Depends(auth)):
    return request.app.state.console_store.setup_state()


@router.post("/api/console/setup/complete")
async def complete_setup(request: Request, _client_id: str = Depends(auth)):
    state = request.app.state.console_store.complete_setup()
    request.app.state.bus.broadcast("setup_changed", {"completed": True})
    return {"succeed": True, **state}


@router.post("/api/console/setup/reset")
async def reset_setup(request: Request, client_id: str = Depends(auth)):
    if not request.app.state.sessions.has_site(client_id):
        return JSONResponse(status_code=403, content={"succeed": False, "error": "no_site"})
    state = request.app.state.console_store.reset_setup()
    request.app.state.bus.broadcast("setup_changed", {"completed": False})
    return {"succeed": True, **state}


@router.get("/api/console/status")
async def console_status(request: Request, _client_id: str = Depends(auth)):
    return {"succeed": True, **request.app.state.console.status()}


@router.post("/api/console/drive")
async def console_drive(request: Request, client_id: str = Depends(auth)):
    body = await read_body(request)
    result = await request.app.state.console.drive(
        client_id,
        str(body.get("direction") or ""),
        body.get("trans"),
        start=bool(body.get("start")),
    )
    return _respond(result)


@router.post("/api/console/drive/stop")
async def console_drive_stop(request: Request, client_id: str = Depends(auth)):
    return _respond(await request.app.state.console.drive_stop(client_id))


@router.post("/api/console/avoid")
async def console_avoid(request: Request, client_id: str = Depends(auth)):
    body = await read_body(request)
    result = await request.app.state.console.set_avoid(
        client_id, bool(body.get("enabled")), bool(body.get("confirm"))
    )
    return _respond(result)


@router.post("/api/console/fork")
async def console_fork(request: Request, client_id: str = Depends(auth)):
    body = await read_body(request)
    result = await request.app.state.console.fork(
        client_id,
        str(body.get("axis") or ""),
        str(body.get("mode") or ""),
        delta=body.get("delta"),
        pos=body.get("pos"),
        confirm=bool(body.get("confirm")),
    )
    return _respond(result)


@router.post("/api/console/stop")
async def console_stop(request: Request, client_id: str = Depends(auth)):
    return _respond(await request.app.state.console.soft_stop(client_id))


@router.post("/api/console/estop")
async def console_estop(request: Request, client_id: str = Depends(auth)):
    return _respond(await request.app.state.console.estop(client_id))


@router.post("/api/console/estop/release")
async def console_estop_release(request: Request, client_id: str = Depends(auth)):
    body = await read_body(request)
    return _respond(await request.app.state.console.estop_release(client_id, bool(body.get("confirm"))))


@router.get("/api/console/params")
async def console_params(request: Request, _client_id: str = Depends(auth)):
    try:
        return {"succeed": True, **(await request.app.state.params_svc.get_view())}
    except Exception as e:
        return JSONResponse(status_code=502, content={"succeed": False, "error": str(e)})


@router.post("/api/console/params")
async def console_params_apply(request: Request, client_id: str = Depends(auth)):
    body = await read_body(request)
    result = await request.app.state.params_svc.apply(
        client_id, body.get("changes") or {}, bool(body.get("confirm"))
    )
    return _service_result(result)


@router.get("/api/console/params/backups")
async def console_backups(request: Request, _client_id: str = Depends(auth)):
    return {"succeed": True, "backups": request.app.state.params_svc.list_backups()}


@router.post("/api/console/params/restore")
async def console_restore(request: Request, client_id: str = Depends(auth)):
    body = await read_body(request)
    result = await request.app.state.params_svc.restore(
        client_id, str(body.get("backupId") or ""), bool(body.get("confirm"))
    )
    return _service_result(result)


@router.get("/api/console/params/log")
async def console_param_log(request: Request, _client_id: str = Depends(auth)):
    return {"succeed": True, "log": request.app.state.params_svc.read_log()}
