"""急停强制退出现场（步骤49）。

后台任务每 2s 轮询 jarvis /api/state：
- state.alarm 非 estop → estop 的上升沿：强制结束现场锁（等效 endSite force）
  + broadcast site_changed{holderClientId:null, reason:"estop"}
  + broadcast tts 话术 alarm_estop_exit
- 仅上升沿触发一次；alarm 恢复后重新武装（下次 estop 再触发）
- jarvis 不可达静默跳过；config safety.estop_exit_site=false 关闭
"""
import asyncio

from ..speak import render
from ..tts.piper import synthesize

_POLL_S = 2.0


class AlarmMonitor:
    def __init__(self, app):
        self._app = app
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        cfg = (self._app.state.cfg.get("safety") or {})
        if not cfg.get("estop_exit_site", True):
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        sessions = self._app.state.sessions
        jarvis = self._app.state.jarvis
        bus = self._app.state.bus
        cfg = self._app.state.cfg
        was_estop = False
        try:
            while True:
                await asyncio.sleep(_POLL_S)
                try:
                    state = await jarvis.get_state()
                except Exception:
                    continue  # jarvis 不可达静默跳过
                alarm = state.get("alarm") if isinstance(state, dict) else None
                if alarm == "estop" and not was_estop:
                    was_estop = True
                    # 上升沿：强制退出现场（仅当存在现场锁时才有意义，end_site 幂等）
                    sessions.end_site("", force=True)
                    bus.broadcast("site_changed", {"holderClientId": None, "reason": "estop"})
                    text = render("alarm_estop_exit")
                    try:
                        spoken = await synthesize(text, "fail", cfg)
                        audio = spoken.get("audio_base64")
                    except Exception:
                        audio = None
                    bus.broadcast(
                        "tts",
                        {"text": text, "style": "fail", "target": "both",
                         "audioBase64": audio, "clientId": None},
                    )
                    print("[forkai-core] 检测到急停，已强制退出现场模式")
                elif alarm != "estop":
                    was_estop = False  # 恢复后重新武装
        except asyncio.CancelledError:
            pass
