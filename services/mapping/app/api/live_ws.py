"""实时预览。文本帧是 JSON，二进制帧是地图 PNG。"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/api/v1/mapping/tasks/{task_id}/live")
async def live(websocket: WebSocket, task_id: str):
    await websocket.accept()
    ctx = websocket.app.state.ctx
    if ctx.tasks.load(task_id) is None:
        await websocket.close(code=4404)
        return
    await ctx.live.attach(task_id, websocket)
    try:
        while True:
            await websocket.receive()
    except WebSocketDisconnect:
        pass
    finally:
        ctx.live.detach(task_id, websocket)
