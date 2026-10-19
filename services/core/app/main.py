"""FastAPI 入口（对齐 voice-gateway src/index.ts）。

- CORS 全开；挂载 pair/site/voice/robot 路由与 WS 路由
- 静态托管 apps/web/dist（目录不存在时跳过）
- 401 统一返回 {"succeed":false,"error":"unpaired"}
- 启动日志：[forkai-core] http://host:port vehicle=xxx jarvis=xxx
"""
import asyncio
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import routes_flows, routes_pair, routes_robot, routes_site, routes_trace, routes_voice, ws_proxy
from .api.deps import UnpairedError
from .config import load_config
from .events import EventBus
from .executor import IntentExecutor
from .jarvis.client import JarvisClient
from .nlu.llm import LLMClient
from .safety.alarm_monitor import AlarmMonitor
from .safety.watchdog import MotionWatchdog
from .session.manager import SessionManager
from .taskflow.engine import FlowEngine
from .taskflow.store import FlowStore
from .trace_observer import StateObserver
from .tracer import Tracer
from .asr.cloud import close_asr_client
from .g2a import close_g2a_client
from .tts.cloud import close_client as close_tts_cloud
from .tts.prewarm import attach_prewarm_state, start_prewarm, stop_prewarm

cfg = load_config()

app = FastAPI(title="forkai-core")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

bus = EventBus()
sessions = SessionManager(cfg)
jarvis = JarvisClient(cfg)
_drive = cfg.get("drive") or {}
watchdog = MotionWatchdog(
    jarvis,
    cfg["watchdogMs"],
    cfg["speed"]["max"],
    cfg["speed"]["default"],
    on_stop=lambda: bus.broadcast("watchdog_stop", {}),
    step=int(cfg["speed"].get("step", 100)),
    control_speed=int(_drive.get("control_speed", 50)),
    rot_mag=int(_drive.get("rot", 50)),
)
executor = IntentExecutor(cfg, sessions, jarvis, watchdog)
llm = LLMClient(cfg)
alarm_monitor = AlarmMonitor(app)
flow_store = FlowStore()
_trace_cfg = cfg.get("trace") or {}
try:
    _trace_cap = int(_trace_cfg.get("capacity", 500))
except (TypeError, ValueError):
    _trace_cap = 500
tracer = Tracer(bus, _trace_cap)
bus.add_listener(tracer.on_bus_event)
executor.set_tracer(tracer)
flow_engine = FlowEngine(cfg, flow_store, jarvis, bus, tracer=tracer)
executor.set_flow_engine(flow_engine)
executor.set_flow_store(flow_store)
state_observer = StateObserver(cfg, jarvis, tracer)

app.state.cfg = cfg
app.state.bus = bus
app.state.sessions = sessions
app.state.jarvis = jarvis
app.state.watchdog = watchdog
app.state.executor = executor
app.state.llm = llm
app.state.flow_store = flow_store
app.state.flow_engine = flow_engine
app.state.tracer = tracer
attach_prewarm_state(app)


@app.exception_handler(UnpairedError)
async def _unpaired_handler(_request, _exc):
    return JSONResponse(status_code=401, content={"succeed": False, "error": "unpaired"})


@app.get("/api/health")
async def health():
    return {"ok": True, "vehicleId": cfg["vehicleId"], "watchdogMs": cfg["watchdogMs"]}


app.include_router(routes_pair.router)
app.include_router(routes_site.router)
app.include_router(routes_voice.router)
app.include_router(routes_robot.router)
app.include_router(routes_flows.router)
app.include_router(routes_trace.router)
app.include_router(ws_proxy.router)

# 静态托管前端构建产物（对齐 index.ts express.static）
_web_dist = (Path(__file__).resolve().parent.parent.parent.parent / "apps" / "web" / "dist")
if _web_dist.is_dir():
    _index_html = _web_dist / "index.html"

    @app.get("/")
    async def _web_index():
        return FileResponse(_index_html, headers={"Cache-Control": "no-store, max-age=0"})

    app.mount("/", StaticFiles(directory=str(_web_dist), html=True), name="web")


@app.on_event("startup")
async def _startup_log():
    print(
        f"[forkai-core] http://{cfg['server']['host']}:{cfg['server']['port']} "
        f"vehicle={cfg['vehicleId']} jarvis={cfg['jarvis']['baseUrl']}"
    )
    await alarm_monitor.start()
    await state_observer.start()
    await flow_engine.restore_snapshot()
    tts_cfg = cfg.get("tts") or {}
    if tts_cfg.get("prewarm_on_startup"):
        start_prewarm(app)


@app.on_event("shutdown")
async def _shutdown():
    await stop_prewarm(app)
    await state_observer.stop()
    await alarm_monitor.stop()
    await flow_engine.shutdown()
    await llm.close()
    await jarvis.close()
    await close_tts_cloud()
    await close_asr_client()
    await close_g2a_client()
