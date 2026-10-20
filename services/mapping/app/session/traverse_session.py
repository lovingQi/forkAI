"""3D 会话。在线、离线和增量都走这里。核对点 F 未关闭。"""
import asyncio
import json
import shutil
from pathlib import Path

from ..engine.process import EngineProcess
from ..engine.traverse_client import TraverseClient
from ..errors import MappingError
from ..recording.reader import open_recording


class TraverseSession:
    def __init__(self, ctx, task):
        self.ctx = ctx
        self.task = task
        self.action = None
        self.wake = asyncio.Event()
        self.client = None
        self.engine = None
        self.closing = False
        self.background = []

    def request(self, action: str) -> None:
        self.action = action
        self.wake.set()

    async def run(self) -> None:
        try:
            await self._bringup()
            await self._loop()
        except MappingError as exc:
            await self._fail(exc)
        except Exception as exc:
            await self._fail(MappingError("MAPPING_TASK_START_FAILED", "3D 建图失败", diagnostic=repr(exc)))
        finally:
            await self._teardown()
            await self.ctx.scheduler.release(self.task.id)

    async def _bringup(self) -> None:
        cfg = self.ctx.cfg["engines"]["traverse"]
        root = Path(cfg["root"])
        manifest = json.loads((root / "runtime.json").read_text(encoding="utf-8"))
        self._set("starting")
        workspace = Path(self.ctx.cfg["dataDir"]) / "tasks" / self.task.id / "engine"
        workspace.mkdir(parents=True, exist_ok=True)
        self.engine = EngineProcess(
            executable=str(root / manifest["executable"]),
            arguments=[f"--listen_address={cfg.get('endpoint', '127.0.0.1:50052')}", f"--workspace={workspace}"],
            cwd=str(root),
            env={"FORKAI_FAKE_TRAVERSE_WORKSPACE": str(workspace), "FORKAI_FAKE_TRAVERSE_VERSION": manifest.get("engineVersion", "test"), "FORKAI_FAKE_TRAVERSE_BUILD": manifest.get("buildId", "test")},
            log_path=workspace.parent / "engine.log",
            ready_timeout=cfg.get("readyTimeoutMs", 30000) / 1000,
            stop_timeout=5,
            kill_timeout=5,
            endpoint=cfg.get("endpoint", "127.0.0.1:50052"),
        )
        await self.engine.start()
        self.client = TraverseClient(cfg.get("endpoint", "127.0.0.1:50052"), root / manifest.get("protoRoot", "proto"), Path(self.ctx.cfg["dataDir"]) / "proto_gen")
        info = await self.client.server_info()
        objective = (self.task.objective or {}).get("kind")
        self.client.assert_compatible(info, self.task.mode, None if objective in (None, "create") else objective)
        definition = {
            "clientId": "forkai",
            "pipeline": "lidar-inertial-3d",
            "mode": self.task.mode,
            "profileRevision": manifest.get("profileRevision", "1"),
            "mapId": self.task.id,
            "inputs": self.task.inputs,
            "objective": self.task.objective,
        }
        await self.client.create(definition)
        self._set("running")
        if (self.task.source or {}).get("kind") == "recording":
            self.background.append(asyncio.create_task(self._feed_recording()))
        elif objective not in (None, "create"):
            self.background.append(asyncio.create_task(self._feed_recording()))

    async def _feed_recording(self) -> None:
        meta = self.ctx.recordings.get(self.task.source["recordingId"])
        _summary, messages = open_recording(meta["path"], [])
        sequence = 0
        count = 0
        objective = (self.task.objective or {}).get("kind")
        for _item in messages():
            count += 1
            if count >= 16:
                sequence += 1
                await self.client.add_batch(sequence, count)
                count = 0
                if objective not in (None, "create") and self.task.alignmentPhase != "confirmed":
                    state = await self.client.align(False)
                    if state == "failed":
                        raise MappingError("MAPPING_INPUT_INVALID", "3D 对齐失败，请调整初始位姿")
                    if state == "ready":
                        self.task.alignmentPhase = "awaiting-confirmation"
                        self._set("paused")
                        while self.task.alignmentPhase == "awaiting-confirmation" and not self.closing:
                            await asyncio.sleep(0.1)
        if count:
            sequence += 1
            await self.client.add_batch(sequence, count)
        self.task.stopReason = "end_of_input"
        self._set("ready-to-save")
        self.wake.set()

    async def _loop(self) -> None:
        while self.task.status in {"running", "paused", "ready-to-save"}:
            try:
                await asyncio.wait_for(self.wake.wait(), 0.3)
            except asyncio.TimeoutError:
                pass
            self.wake.clear()
            if self.action == "confirm" and self.task.alignmentPhase == "awaiting-confirmation":
                await self.client.align(True)
                self.task.alignmentPhase = "confirmed"
                self._set("running")
                self.action = None
            if self.action == "cancel":
                self.closing = True
                await self.client.delete()
                self._set("cancelled")
                return
            if self.action == "finish" and self.task.status in {"running", "ready-to-save"}:
                await self._finish()
                return
            if self.task.mode == "online" and self.task.status == "running" and self.action is None:
                # 在线假引擎没有真实点云流时，测试用 finish 收尾。
                continue

    async def _finish(self) -> None:
        self.closing = True
        self._set("finishing")
        await self.client.finish()
        self._set("exporting")
        await self.client.export()
        workspace = Path(self.ctx.cfg["dataDir"]) / "tasks" / self.task.id / "engine"
        staging = workspace.parent / "staging"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir()
        for name in ("map_2d.json", "global.pcd"):
            shutil.copy2(workspace / name, staging / name)
        shutil.copytree(workspace / "map_project", staging / "map_project")
        project_id = self.ctx.maps.commit_3d(self.task.to_dict(), staging, self.ctx.cfg["foxglove"]["url"], self.ctx.cfg["robot"]["resourceId"])
        self.task.projectId = project_id
        await self.client.delete()
        self._set("completed")

    async def _fail(self, exc: MappingError) -> None:
        if self.task.status not in {"completed", "cancelled", "failed", "interrupted"}:
            self.task.error = exc.body()["error"]
            self._set("failed")

    async def _teardown(self) -> None:
        for task in self.background:
            task.cancel()
        if self.client:
            await self.client.close()
        if self.engine:
            await self.engine.stop()

    def _set(self, status: str) -> None:
        self.task.status = status
        self.ctx.tasks.save(self.task)
        self.ctx.activity(self.task.id).append("status", {"status": status})
