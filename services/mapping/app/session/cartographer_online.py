"""2D 在线会话：拉起引擎、接入 Bridge、预览、完成或取消。"""
import asyncio
import hashlib
import json
import queue
import shutil
import threading
import time
from pathlib import Path

from ..engine.cartographer_client import CartographerClient
from ..engine.cartographer_profile import load_runtime_profile, product_by_id
from ..engine.process import EngineProcess
from ..errors import MappingError
from ..feed.convert import convert_laser_scan, convert_odometry
from ..feed.feeder import CLIENT_ID, run_feeder, run_recorded_feeder
from ..feed.pause_gate import PauseGate
from ..incremental.plan2d import initial_relative_pose, json_pose_to_state, json_region_to_state
from ..recording.reader import open_recording
from ..geom import compose, from_universal_ticks, transform_point, yaw_of
from ..ingest.foxglove_client import FoxgloveClient
from ..ingest.observe import extrinsic_for, observe_topics
from ..ingest.online_stream import OnlineStream
from ..ingest.ros1_codec import decode
from ..preview.png import encode_gray8, occupancy_gray

_ACTIVE = {"running", "paused", "ready-to-save"}
_SLAM_VIZ_S = 0.2


def _ros_stamp(decoded: dict) -> tuple[int, str]:
    stamp = decoded["header"]["stamp"]
    sec = int(stamp["sec"])
    nsec = int(stamp.get("nsec") or stamp.get("nanosec") or 0)
    frame = str(decoded["header"].get("frame_id") or "")
    return sec * 1_000_000_000 + nsec, frame


def _offer(incoming: queue.Queue, item) -> None:
    try:
        incoming.put_nowait(item)
        return
    except queue.Full:
        pass
    try:
        incoming.get_nowait()
    except queue.Empty:
        return
    try:
        incoming.put_nowait(item)
    except queue.Full:
        return


class OnlineSession:
    def __init__(self, ctx, task):
        self.ctx = ctx
        self.task = task
        self.stream: OnlineStream | None = None
        self.engine: EngineProcess | None = None
        self.client: CartographerClient | None = None
        self.action: str | None = None
        self.wake = asyncio.Event()
        self.background: list[asyncio.Task] = []
        self.background_error: Exception | None = None
        self.last_result_at = 0.0
        self.result_time_ns: int | None = None
        self.data_time_ns: int | None = None
        self.behind_since: float | None = None
        self.last_preview: dict | None = None
        self.trajectory_id: int | None = None
        self.closing = False
        self.feeder_task: asyncio.Task | None = None
        self.points: list[list[float]] = []
        self._last_viz = 0.0
        self._obstacle_mm: set[tuple[int, int]] = set()
        self.submitted = 0
        self.staging: Path | None = None
        self.runtime: dict | None = None
        self.bindings: dict = {}

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
            await self._fail(MappingError("MAPPING_TASK_START_FAILED", "建图失败", diagnostic=repr(exc)))
        finally:
            await self._teardown()
            await self.ctx.scheduler.release(self.task.id)

    async def _bringup(self) -> None:
        cfg = self.ctx.cfg
        eng = cfg["engines"]["cartographer"]
        product = product_by_id(self.ctx.profiles, self.task.profileId)
        self.runtime = load_runtime_profile(Path(eng["profileRoot"]), product["runtimeRevision"])
        self._set_status("starting")
        urdf_path = self.ctx.urdf_path
        profile_path = Path(eng["profileRoot"]) / f"{product['runtimeRevision']}.json"
        self.task.snapshot = {
            "urdfSha256": hashlib.sha256(urdf_path.read_bytes()).hexdigest(),
            "profileSha256": hashlib.sha256(profile_path.read_bytes()).hexdigest(),
            "engineVersion": eng.get("engineVersion") or "",
            "buildId": eng.get("buildId") or "",
        }
        self.staging = Path(cfg["dataDir"]) / "tasks" / self.task.id / "staging"
        if self.staging.exists():
            shutil.rmtree(self.staging)
        self.staging.mkdir(parents=True)
        self.engine = EngineProcess(
            executable=eng["bin"],
            arguments=[
                f"--configuration_directory={eng['configDir']}",
                f"--configuration_basename={eng['configBasename']}",
                "--logtostderr=true",
            ],
            cwd=eng["configDir"],
            env={},
            log_path=Path(cfg["dataDir"]) / "tasks" / self.task.id / "engine.log",
            ready_timeout=eng["readyTimeoutMs"] / 1000,
            stop_timeout=eng["stopTimeoutMs"] / 1000,
            kill_timeout=eng["killTimeoutMs"] / 1000,
            endpoint=eng["endpoint"],
        )
        await self.engine.start()
        self.client = CartographerClient(eng["endpoint"], Path(eng["protoRoot"]), Path(cfg["dataDir"]) / "proto_gen")
        version = await self.client.incremental_protocol_version()
        if version not in (None, 1):
            raise MappingError("MAPPING_ENGINE_INCOMPATIBLE", f"增量协议版本是 {version}，需要 1")
        tracking = (self.runtime.get("range") or {}).get("trackingFrame") or "base_link"
        specs = [{"topic": item["topic"], "kind": "range"} for item in self.task.inputs["ranges"]]
        odom = self.task.inputs.get("odometry")
        if odom:
            specs.append({"topic": odom["topic"], "kind": "odometry"})
        if (self.task.source or {}).get("kind") == "recording":
            observed = None
        else:
            observed = await observe_topics(
                cfg["foxglove"]["url"], specs, cfg["foxglove"]["startTimeoutMs"] / 1000, self.ctx.urdf, tracking,
            )
        if odom and observed:
            child = observed[odom["topic"]]["child"]
            expected = (self.runtime.get("odometry") or {}).get("childFrame")
            if expected and child and child != str(expected).lstrip("/"):
                raise MappingError("MAPPING_CALIBRATION_MISSING", "里程计子坐标系与参数不一致")
        sensors = [{"id": item["sensorId"], "type": "RANGE"} for item in self.task.inputs["ranges"]]
        if odom:
            sensors.append({"id": (product["inputs"]["odometry"] or {}).get("sensorId") or "odometry", "type": "ODOMETRY"})
        options = json.loads(json.dumps(self.runtime["trajectoryBuilderOptions"]))
        if (self.task.objective or {}).get("kind") not in (None, "create"):
            options = await self._incremental_options(options)
        self.trajectory_id = await self.client.add_trajectory(sensors, options, CLIENT_ID)
        self.gate = PauseGate()
        if (self.task.source or {}).get("kind") == "recording":
            await self._start_recording(specs, tracking, odom)
            return
        topics = [spec["topic"] for spec in specs]
        self.stream = OnlineStream(topics)
        self.stream.arm()
        extrinsics = {
            item["topic"]: extrinsic_for(self.ctx.urdf, tracking, observed[item["topic"]]["frame"])
            for item in self.task.inputs["ranges"]
        }
        if odom:
            extrinsics[odom["topic"]] = extrinsic_for(self.ctx.urdf, tracking, observed[odom["topic"]]["child"] or tracking)
        self.bindings = {}
        for item in self.task.inputs["ranges"]:
            sensor_id = item["sensorId"]
            topic = item["topic"]
            extrinsic = extrinsics[topic]

            def convert_range(message, sensor_id=sensor_id, extrinsic=extrinsic):
                return convert_laser_scan(message["decoded"], sensor_id, extrinsic, self.trajectory_id, CLIENT_ID, message["stamp_ns"])

            self.bindings[topic] = {"convert": convert_range}
        if odom:
            sensor_id = sensors[-1]["id"]
            topic = odom["topic"]
            extrinsic = extrinsics[topic]

            def convert_odom(message, sensor_id=sensor_id, extrinsic=extrinsic):
                return convert_odometry(message["decoded"], sensor_id, extrinsic, self.trajectory_id, CLIENT_ID, message["stamp_ns"]), 0

            self.bindings[topic] = {"convert": convert_odom}
        self.background.append(asyncio.create_task(self._guard(self._read_bridge(cfg["foxglove"]["url"], specs))))
        self.feeder_task = asyncio.create_task(self._guard(run_feeder(
            self.stream, self.client, self.trajectory_id, self.bindings, self.runtime["limits"], self._on_progress,
        )))
        self.background.append(self.feeder_task)
        self.background.append(asyncio.create_task(self._guard(self._slam())))
        self.background.append(asyncio.create_task(self._guard(self._preview())))
        self.last_result_at = time.monotonic()
        self._set_status("running")

    async def _loop(self) -> None:
        while self.task.status in _ACTIVE:
            try:
                await asyncio.wait_for(self.wake.wait(), 0.25)
            except asyncio.TimeoutError:
                pass
            self.wake.clear()
            if self.background_error:
                raise self.background_error
            if self.engine and not self.engine.alive() and not self.closing:
                raise MappingError("MAPPING_TASK_INTERRUPTED", "引擎进程退出")
            self._watchdog()
            if self.stream and self.stream.stopped and self.task.status == "running" and self.stream.stop_reason not in ("finished", "cancelled"):
                await self._wait_feeder()
                if self.engine and self.engine.alive():
                    self.task.stopReason = self.stream.stop_reason
                    self._set_status("ready-to-save")
                else:
                    raise MappingError("MAPPING_TASK_INTERRUPTED", "输入中断时引擎已退出")
            if self.action == "pause" and self.task.status == "running" and (self.task.source or {}).get("kind") == "recording":
                self.gate.pause()
                self._set_status("paused")
                self.action = None
            if self.action == "resume" and self.task.status == "paused" and self.task.alignmentPhase != "awaiting-confirmation":
                self.gate.resume()
                self._set_status("running")
                self.action = None
            if self.action == "confirm" and self.task.alignmentPhase == "awaiting-confirmation":
                self.task.alignmentPhase = "confirmed"
                self.gate.resume()
                self._set_status("running")
                self.action = None
            if self.action == "cancel":
                await self._cancel()
                return
            if self.action == "finish":
                await self._finish()
                return
            self._touch_stats()

    def _watchdog(self) -> None:
        if (self.task.source or {}).get("kind") == "recording":
            return
        if self.task.status != "running" or self.stream is None or self.closing:
            return
        now = time.monotonic()
        if self.last_result_at and now - self.last_result_at > 10 and not self.stream.input_fresh():
            self.stream.stop("engine_progress_stalled")

    def _umcl_from_scans(self) -> dict:
        cells = set(self._obstacle_mm)
        if not cells and self.points:
            cells = {(int(round(point[0] * 1000)), int(round(point[1] * 1000))) for point in self.points}
        if not cells:
            return {
                "Header": "umcl-map",
                "MapRes": 0.05,
                "MinPose": "0 0",
                "MaxPose": "1 1",
                "NumPoints": 1,
                "ObsPoints": [[0, 0]],
            }
        columns: dict[int, list[int]] = {}
        for x, y in cells:
            columns.setdefault(x, []).append(y)
        obs = [[x, *sorted(set(ys))] for x, ys in sorted(columns.items())]
        count = sum(len(column) - 1 for column in obs)
        xs = [column[0] for column in obs]
        ys = [value for column in obs for value in column[1:]]
        return {
            "Header": "umcl-map",
            "MapRes": 0.05,
            "MinPose": f"{min(xs)} {min(ys)}",
            "MaxPose": f"{max(xs)} {max(ys)}",
            "NumPoints": count,
            "ObsPoints": obs,
        }

    async def _finish(self) -> None:
        if self.task.status not in _ACTIVE:
            return
        self.closing = True
        self._set_status("finishing")
        assert self.stream and self.client and self.staging and self.trajectory_id is not None
        self.stream.stop("finished")
        await self._wait_feeder()
        await self.client.finish_trajectory(self.trajectory_id, CLIENT_ID)
        await self.client.run_final_optimization()
        self._set_status("exporting")
        exported = self.staging / "map.mcap"
        await self.client.write_state_to_file(str(exported))
        if not (self.staging / "map.json").is_file():
            (self.staging / "map.json").write_text(
                json.dumps(self._umcl_from_scans(), ensure_ascii=False),
                encoding="utf-8",
            )
        exported.rename(self.staging / "mapping_state.mcap")
        objective = self.task.objective or {}
        if objective.get("kind") not in (None, "create"):
            from ..incremental.plan2d import transform_incremental_map
            base = self.ctx.maps.directory(objective["baseProjectId"])
            transform = json.loads((base / "project.json").read_text(encoding="utf-8")).get("map2dFromLocalization") or {}
            current = json.loads((self.staging / "map.json").read_text(encoding="utf-8"))
            (self.staging / "map.json").write_text(json.dumps(transform_incremental_map(current, transform)), encoding="utf-8")
        (self.staging / "online-input.json").write_text(json.dumps({
            "schemaVersion": 1,
            "stopReason": self.task.stopReason,
            "stats": self.task.stats,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        (self.staging / "mapping-profile.json").write_text(
            json.dumps(product_by_id(self.ctx.profiles, self.task.profileId), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        shutil.copy2(self.ctx.urdf_path, self.staging / "robot.urdf")
        try:
            await self.client.delete_trajectory(self.trajectory_id, CLIENT_ID)
        except Exception:
            pass
        project_id = self.ctx.maps.commit(
            self.task.to_dict(),
            self.staging,
            self.ctx.cfg["foxglove"]["url"],
            self.ctx.cfg["robot"]["resourceId"],
            self.last_preview,
        )
        self.task.projectId = project_id
        self._set_status("completed")

    async def _cancel(self) -> None:
        self.closing = True
        if self.stream:
            self.stream.stop("cancelled")
        await self._wait_feeder()
        if self.client and self.trajectory_id is not None:
            await self.client.delete_trajectory(self.trajectory_id, CLIENT_ID)
        self._set_status("cancelled")

    async def _fail(self, exc: MappingError) -> None:
        if self.task.status in {"completed", "cancelled", "failed", "interrupted"}:
            return
        self.closing = True
        if self.stream:
            self.stream.stop("failed")
        self.task.error = exc.body()["error"]
        self.task.stopReason = exc.code
        self.ctx.activity(self.task.id).append("diagnostic", {"message": exc.diagnostic, "diagnosticId": exc.diagnostic_id})
        self._set_status("failed")

    async def _teardown(self) -> None:
        for task in self.background:
            task.cancel()
        for task in self.background:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if self.client:
            await self.client.close()
        if self.engine:
            await self.engine.stop()
        await self._publish()

    def _note_input(self, topic: str) -> None:
        if self.stream is not None:
            self.stream.note_seen(topic)

    async def _read_bridge(self, url: str, specs: list[dict]) -> None:
        incoming: queue.Queue = queue.Queue(maxsize=1000)
        done = threading.Event()

        def runner() -> None:
            try:
                asyncio.run(self._ingest_bridge(url, specs, incoming, done))
            except Exception as exc:
                _offer(incoming, ("error", exc))

        def watch() -> None:
            while not done.is_set():
                stream = self.stream
                if stream is not None:
                    stream.poll_silence()
                    if stream.stop_reason == "input_stalled":
                        _offer(incoming, ("stop", "input_stalled"))
                        return
                done.wait(0.05)

        thread = threading.Thread(target=runner, name="mapping-bridge", daemon=True)
        watcher = threading.Thread(target=watch, name="mapping-input-watch", daemon=True)
        thread.start()
        watcher.start()
        try:
            while self.stream and not self.stream.stopped:
                try:
                    kind, payload = await asyncio.get_running_loop().run_in_executor(None, lambda: incoming.get(timeout=0.2))
                except queue.Empty:
                    self.stream.poll_silence()
                    continue
                if kind == "error":
                    raise payload
                if kind == "stop":
                    self.stream.stop(payload)
                    break
                topic, schema, raw = payload
                decoded = decode(schema, raw)
                stamp, frame = _ros_stamp(decoded)
                self.stream.push(topic, stamp, frame, decoded, len(raw))
                self.data_time_ns = stamp
                self.wake.set()
                self.stream.poll_silence()
        finally:
            done.set()
            thread.join(timeout=2)
            watcher.join(timeout=1)

    async def _ingest_bridge(self, url: str, specs: list[dict], incoming: queue.Queue, done: threading.Event) -> None:
        client = FoxgloveClient(url)
        client.on_input = self._note_input
        await client.connect()
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not done.is_set():
                if all(self._match(client, spec) for spec in specs):
                    break
                await client.next_message(0.2)
            else:
                if not done.is_set():
                    raise MappingError("MAPPING_INPUT_INVALID", "开始后没有再次看到全部话题")
                return
            await client.subscribe([int(self._match(client, spec)["id"]) for spec in specs])
            assert self.stream is not None
            while not done.is_set() and not self.stream.stopped:
                event = await client.next_message(0.2)
                if event is None:
                    continue
                if event.get("kind") == "closed":
                    _offer(incoming, ("stop", "bridge_disconnected"))
                    break
                if event.get("kind") == "unadvertise":
                    _offer(incoming, ("stop", "input_stalled"))
                    break
                if event.get("kind") != "data":
                    continue
                channel = client.channels.get(event["channelId"])
                if not channel:
                    continue
                _offer(incoming, ("data", (channel["topic"], channel.get("schemaName") or "", event["payload"])))
        finally:
            await client.close()

    def _match(self, client: FoxgloveClient, spec: dict):
        from ..ingest.observe import _channel_for
        return _channel_for(client, spec)

    async def _slam(self) -> None:
        assert self.client and self.trajectory_id is not None
        async for response in self.client.local_slam(self.trajectory_id):
            if self.closing:
                break
            self.last_result_at = time.monotonic()
            self.result_time_ns = from_universal_ticks(int(response.timestamp))
            if time.monotonic() - self._last_viz < _SLAM_VIZ_S:
                continue
            self._last_viz = time.monotonic()
            local_pose = {
                "translation": {"x": response.local_pose.translation.x, "y": response.local_pose.translation.y, "z": response.local_pose.translation.z},
                "rotation": {"x": response.local_pose.rotation.x, "y": response.local_pose.rotation.y, "z": response.local_pose.rotation.z, "w": response.local_pose.rotation.w or 1},
            }
            local_to_global = await self.client.local_to_global(self.trajectory_id)
            pose = compose(local_to_global, local_pose)
            yaw = yaw_of(pose["rotation"])
            self.points.append([pose["translation"]["x"], pose["translation"]["y"]])
            if len(self.points) > 5000:
                self.points = self.points[::2]
            scan = []
            for point in response.range_data.returns[:2000]:
                mapped = transform_point(local_to_global, {"x": point.position.x, "y": point.position.y, "z": point.position.z})
                scan.append([mapped["x"], mapped["y"]])
                if len(self._obstacle_mm) < 200_000:
                    self._obstacle_mm.add((int(round(mapped["x"] / 0.05)) * 50, int(round(mapped["y"] / 0.05)) * 50))
            await self.ctx.live.publish_text(self.task.id, "pose", {
                "type": "pose", "x": pose["translation"]["x"], "y": pose["translation"]["y"], "yaw": yaw, "t": self.result_time_ns,
            })
            await self.ctx.live.publish_text(self.task.id, "trajectory", {"type": "trajectory", "points": self.points})
            await self.ctx.live.publish_text(self.task.id, "scan", {"type": "scan", "points": scan})

    def _render_preview(self):
        grid = self.client.capture_grid_blocking(self.trajectory_id)
        if not grid:
            return None
        pixels = occupancy_gray(grid["cells"])
        return grid, encode_gray8(grid["width"], grid["height"], pixels)

    async def _preview(self) -> None:
        await asyncio.sleep(1.0)
        while not self.closing:
            try:
                payload = await asyncio.to_thread(self._render_preview)
                if payload:
                    grid, png = payload
                    self.last_preview = grid
                    await self.ctx.live.publish_map(self.task.id, {
                        "type": "map",
                        "version": int(time.time() * 1000),
                        "resolution": grid["resolution"],
                        "originX": grid["origin"]["x"],
                        "originY": grid["origin"]["y"],
                        "width": grid["width"],
                        "height": grid["height"],
                    }, png)
            except Exception as exc:
                self.ctx.activity(self.task.id).append("diagnostic", {"message": str(exc)})
            await asyncio.sleep(1.0)

    def _on_progress(self, scans: int, odoms: int, stamp_ns: int | None = None) -> None:
        self.submitted = scans + odoms
        total = getattr(self, "recording_span", None)
        if stamp_ns and total:
            start, end = total
            if end and end > start:
                self.task.progress = max(0.0, min(1.0, (stamp_ns - start) / (end - start)))
        self._touch_stats()

    async def _incremental_options(self, options: dict) -> dict:
        objective = self.task.objective
        base = self.ctx.maps.directory(objective["baseProjectId"])
        transform = json.loads((base / "project.json").read_text(encoding="utf-8")).get("map2dFromLocalization") or {}
        await self.client.load_frozen_state(str(base / "mapping_state.mcap"))
        if objective["kind"] == "update-region":
            selected, base_count = await self.client.select_submaps(json_region_to_state(objective["region"], transform))
            if not selected:
                raise MappingError("MAPPING_INPUT_INVALID", "所选范围没有旧地图", diagnostic="INCREMENTAL_REGION_EMPTY")
            if len(selected) >= base_count:
                raise MappingError("MAPPING_INPUT_INVALID", "范围太大，没有留下对齐参考", diagnostic="INCREMENTAL_REGION_NO_REFERENCE")
            await self.client.trim_submaps(selected)
        target = json_pose_to_state(objective["initialPose"], transform)
        options["initialTrajectoryPose"] = {
            "toTrajectoryId": 0,
            "timestamp": 1,
            "relativePose": initial_relative_pose(target, {"x": 0.0, "y": 0.0, "yaw": 0.0}),
        }
        return options

    async def _checkpoint(self, scans: int) -> None:
        if self.task.alignmentPhase == "confirmed" or (self.task.objective or {}).get("kind") in (None, "create"):
            return
        await self.client.run_final_optimization()
        if await self.client.alignment_matched(self.trajectory_id):
            self.gate.pause()
            self.task.alignmentPhase = "awaiting-confirmation"
            self._set_status("paused")
            return
        if scans >= 1000:
            raise MappingError("MAPPING_TASK_START_FAILED", "新数据没有和旧地图对齐")

    async def _start_recording(self, specs, tracking, odom) -> None:
        meta = self.ctx.recordings.get(self.task.source["recordingId"])
        if not meta:
            raise MappingError("MAPPING_INPUT_INVALID", "录包不存在")
        summary, messages = open_recording(meta["path"], [spec["topic"] for spec in specs])
        self.recording_span = (summary.get("startTimeNs"), summary.get("endTimeNs"))
        observed = {}
        for item in messages():
            observed.setdefault(item["topic"], item)
            if len(observed) == len(specs):
                break
        extrinsics = {
            item["topic"]: extrinsic_for(self.ctx.urdf, tracking, observed[item["topic"]]["frame"])
            for item in self.task.inputs["ranges"]
        }
        self.bindings = {}
        for item in self.task.inputs["ranges"]:
            sensor_id, topic, extrinsic = item["sensorId"], item["topic"], extrinsics[item["topic"]]

            def convert_range(message, sensor_id=sensor_id, extrinsic=extrinsic):
                return convert_laser_scan(message["decoded"], sensor_id, extrinsic, self.trajectory_id, CLIENT_ID, message["stamp_ns"])

            self.bindings[topic] = {"convert": convert_range}
        if odom:
            self.bindings[odom["topic"]] = {"convert": lambda message, sensor_id="odometry": (
                convert_odometry(message["decoded"], sensor_id, {"translation": {"x": 0, "y": 0, "z": 0}, "rotation": {"x": 0, "y": 0, "z": 0, "w": 1}}, self.trajectory_id, CLIENT_ID, message["stamp_ns"]),
                0,
            )}
        self.stream = _RecordControl()
        summary2, messages2 = open_recording(meta["path"], [spec["topic"] for spec in specs])
        self.feeder_task = asyncio.create_task(self._guard(self._recorded(messages2, summary2)))
        self.background.append(self.feeder_task)
        self.background.append(asyncio.create_task(self._guard(self._slam())))
        self.background.append(asyncio.create_task(self._guard(self._preview())))
        self.last_result_at = time.monotonic()
        self._set_status("running")

    async def _recorded(self, messages, summary) -> None:
        await run_recorded_feeder(
            messages, self.client, self.trajectory_id, self.bindings, self.runtime["limits"], self.gate,
            self._on_progress, self._checkpoint if (self.task.objective or {}).get("kind") not in (None, "create") else None,
        )
        if self.stream and self.stream.stop_reason is None:
            self.stream.stop("end_of_input")
        self.wake.set()

    def _touch_stats(self) -> None:
        if self.stream:
            self.task.stats = {
                **self.stream.stats,
                "submitted": self.submitted,
                "droppedRanges": self.stream.stats.get("droppedRanges", 0),
            }
        self.ctx.tasks.save(self.task)

    def _set_status(self, status: str) -> None:
        self.task.status = status
        self.ctx.tasks.save(self.task)
        self.ctx.activity(self.task.id).append("status", {"status": status, "stopReason": self.task.stopReason})
        asyncio.get_running_loop().create_task(self._publish())

    async def _publish(self) -> None:
        await self.ctx.live.publish_text(self.task.id, "state", {"type": "state", "task": self.task.to_dict()})
        await self.ctx.live.publish_text(self.task.id, "stats", {"type": "stats", **self.task.stats})

    async def _wait_feeder(self) -> None:
        if self.feeder_task is None or self.feeder_task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(self.feeder_task), 5)
        except Exception:
            return

    async def _guard(self, coro) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.background_error is None:
                self.background_error = exc if isinstance(exc, MappingError) else MappingError(
                    "MAPPING_TASK_INTERRUPTED", "建图输入中断", diagnostic=repr(exc),
                )
            self.wake.set()


class _RecordControl:
    def __init__(self):
        self.stop_reason = None
        self.stats = {"received": 0, "accepted": 0, "submitted": 0, "rejected": 0, "pending": 0, "droppedRanges": 0}
        self.queue = []

    def stop(self, reason: str) -> None:
        self.stop_reason = reason

    @property
    def stopped(self) -> bool:
        return self.stop_reason is not None

    def poll_silence(self) -> None:
        return None
