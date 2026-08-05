"""任务流 API（步骤40，全部走 auth 依赖）。

- POST /api/flows/{id}/start 需配对即可，不要求现场锁：任务流（TaskFlow）与
  点动（MotionWatchdog）分级——点动是"人拿着遥控器在旁边"，任务流是预先编排的
  自动流程；真车如需加强，可在此加 has_site 校验（一处改动）。
- 低电量/暂停/取消语义见 app/taskflow/engine.py。
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..taskflow.engine import FlowBusyError
from ..taskflow.schema import validate
from ..tasks.schemas import TASK_SCHEMAS
from .deps import auth, read_body

router = APIRouter()

# focklift（点动叉）与 drive（流程内定时点动）不在 TASK_SCHEMAS（按意图建键），
# 这里补全为按 route cmd 建键的参数元信息，供前端动态渲染表单
_EXTRA_NODE_SCHEMAS = {
    "focklift": {
        "route_cmd": "focklift",
        "params": {
            "pos": {"type": "number", "required": True, "safety": True, "ask": "请告诉我目标叉高（毫米）"},
            "wait": {"type": "number", "required": False, "default": 20},
            "tolerance": {"type": "number", "required": False, "default": 20},
        },
    },
    "drive": {
        "route_cmd": "drive",
        "params": {
            "trans": {"type": "number", "required": False, "default": 1},
            "rot": {"type": "number", "required": False, "default": 0},
            "speed": {"type": "number", "required": False, "default": 20},
            "duration_s": {"type": "number", "required": True, "ask": "请告诉我点动时长（秒）"},
        },
    },
}


@router.get("/api/tasks/schemas")
async def task_schemas(_client_id: str = Depends(auth)):
    """节点参数元信息（按 route cmd 建键）：5 任务 + drive。"""
    by_cmd = {s["route_cmd"]: s for s in TASK_SCHEMAS.values()}
    return {"schemas": {**by_cmd, **_EXTRA_NODE_SCHEMAS}}


def _store(request: Request):
    return request.app.state.flow_store


def _engine(request: Request):
    return request.app.state.flow_engine


@router.get("/api/flows")
async def list_flows(request: Request, _client_id: str = Depends(auth)):
    return {"flows": await _store(request).list()}


@router.post("/api/flows")
async def create_flow(request: Request, _client_id: str = Depends(auth)):
    body = await read_body(request)
    errors = validate(body)
    if errors:
        return JSONResponse(status_code=400, content={"succeed": False, "errors": errors})
    fid = await _store(request).create(body)
    return {"succeed": True, "id": fid}


@router.get("/api/flows/{flow_id}")
async def get_flow(flow_id: str, request: Request, _client_id: str = Depends(auth)):
    flow = await _store(request).get(flow_id)
    if not flow:
        return JSONResponse(status_code=404, content={"succeed": False, "error": "not_found"})
    return flow


@router.put("/api/flows/{flow_id}")
async def update_flow(flow_id: str, request: Request, _client_id: str = Depends(auth)):
    body = await read_body(request)
    errors = validate(body)
    if errors:
        return JSONResponse(status_code=400, content={"succeed": False, "errors": errors})
    ok = await _store(request).update(flow_id, body)
    if not ok:
        return JSONResponse(status_code=404, content={"succeed": False, "error": "not_found"})
    return {"succeed": True}


@router.delete("/api/flows/{flow_id}")
async def delete_flow(flow_id: str, request: Request, _client_id: str = Depends(auth)):
    ok = await _store(request).delete(flow_id)
    if not ok:
        return JSONResponse(status_code=404, content={"succeed": False, "error": "not_found"})
    return {"succeed": True}


@router.post("/api/flows/{flow_id}/start")
async def start_flow(flow_id: str, request: Request, _client_id: str = Depends(auth)):
    engine = _engine(request)
    try:
        await engine.start(flow_id)
    except FlowBusyError:
        return JSONResponse(
            status_code=409,
            content={
                "succeed": False,
                "error": "already_running",
                "flowId": engine.status().get("flowId"),
            },
        )
    except KeyError:
        return JSONResponse(status_code=404, content={"succeed": False, "error": "not_found"})
    return {"succeed": True, "flowId": flow_id}


@router.post("/api/flow-engine/pause")
async def pause_flow(request: Request, _client_id: str = Depends(auth)):
    ok = await _engine(request).pause()
    if not ok:
        return JSONResponse(status_code=409, content={"succeed": False, "error": "not_running"})
    return {"succeed": True}


@router.post("/api/flow-engine/resume")
async def resume_flow(request: Request, _client_id: str = Depends(auth)):
    ok = await _engine(request).resume()
    if not ok:
        return JSONResponse(status_code=409, content={"succeed": False, "error": "not_paused"})
    return {"succeed": True}


@router.post("/api/flow-engine/cancel")
async def cancel_flow(request: Request, _client_id: str = Depends(auth)):
    ok = await _engine(request).cancel()
    if not ok:
        return JSONResponse(status_code=409, content={"succeed": False, "error": "not_running"})
    return {"succeed": True}


@router.get("/api/flow-engine/status")
async def flow_status(request: Request, _client_id: str = Depends(auth)):
    return _engine(request).status()
