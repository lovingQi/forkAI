"""FastAPI 入口（对齐 voice-gateway src/index.ts）。

- CORS 全开；挂载 pair/site/voice/robot 路由与 WS 路由
- 静态托管 apps/web/dist（目录不存在时跳过）
- 401 统一返回 {"succeed":false,"error":"unpaired"}
- 启动日志：[forkai-core] http://host:port vehicle=xxx jarvis=xxx
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import routes_flows, routes_pair, routes_robot, routes_site, routes_voice, ws_proxy
from .api.deps import UnpairedError
from .asr.capture import CabinListener
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
watchdog = MotionWatchdog(
    jarvis,
    cfg["watchdogMs"],
    cfg["speed"]["max"],
    cfg["speed"]["default"],
    on_stop=lambda: bus.broadcast("watchdog_stop", {}),
)
executor = IntentExecutor(cfg, sessions, jarvis, watchdog)
llm = LLMClient(cfg)
cabin_listener = CabinListener(app)
alarm_monitor = AlarmMonitor(app)
flow_store = FlowStore()
flow_engine = FlowEngine(cfg, flow_store, jarvis, bus)
executor.set_flow_engine(flow_engine)
executor.set_flow_store(flow_store)

app.state.cfg = cfg
app.state.bus = bus
app.state.sessions = sessions
app.state.jarvis = jarvis
app.state.watchdog = watchdog
app.state.executor = executor
app.state.llm = llm
app.state.flow_store = flow_store
app.state.flow_engine = flow_engine


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
app.include_router(ws_proxy.router)

# 静态托管前端构建产物（对齐 index.ts express.static）
_web_dist = (Path(__file__).resolve().parent.parent.parent.parent / "apps" / "web" / "dist")
if _web_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_web_dist), html=True), name="web")


@app.on_event("startup")
async def _startup_log():
    print(
        f"[forkai-core] http://{cfg['server']['host']}:{cfg['server']['port']} "
        f"vehicle={cfg['vehicleId']} jarvis={cfg['jarvis']['baseUrl']}"
    )
    await cabin_listener.start()
    await alarm_monitor.start()
    await flow_engine.restore_snapshot()


@app.on_event("shutdown")
async def _shutdown():
    await cabin_listener.stop()
    await alarm_monitor.stop()
    await flow_engine.shutdown()
    await llm.close()
    await jarvis.close()
