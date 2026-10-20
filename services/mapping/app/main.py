"""forkai-mapping 入口。只监听 127.0.0.1。"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .api import live_ws, routes
from .config import load_config
from .errors import MappingError
from .runtime import Context

cfg = load_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    ctx = Context(cfg)
    app.state.ctx = ctx
    ctx.tasks.mark_interrupted_on_boot()
    yield
    for session in list(ctx.sessions.values()):
        session.request("cancel")
    await asyncio.sleep(0.2)
    for session in list(ctx.sessions.values()):
        if session.engine:
            await session.engine.stop()


app = FastAPI(title="forkai-mapping", lifespan=lifespan)
app.include_router(routes.router)
app.include_router(live_ws.router)


@app.exception_handler(MappingError)
async def _mapping_error(_request, exc: MappingError):
    return JSONResponse(status_code=exc.status, content=exc.body())


@app.on_event("startup")
async def _log():
    server = cfg["server"]
    print(f"[forkai-mapping] http://{server['host']}:{server['port']}", flush=True)
