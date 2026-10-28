"""Cartographer gRPC 客户端。

字段名按 robospace 客户端的 camelCase 转成 snake_case。
核对点 A/B：同事 proto 未到，本文件只在测试 proto 上验证过。
"""
import gzip
import json
import time

import grpc

from ..errors import MappingError
from ..geom import identity
from ..preview.compositor import MAX_TEXTURE_CACHE_BYTES, compose_occupancy, decode_texture
from .proto_loader import load_proto

_OPTIONS = [
    ("grpc.max_send_message_length", 64 * 1024 * 1024),
    ("grpc.max_receive_message_length", 64 * 1024 * 1024),
]
_SENSOR_TYPE = {"RANGE": 0, "IMU": 1, "ODOMETRY": 2}


def _rigid(msg) -> dict:
    return {
        "translation": {"x": msg.translation.x, "y": msg.translation.y, "z": msg.translation.z},
        "rotation": {"x": msg.rotation.x, "y": msg.rotation.y, "z": msg.rotation.z, "w": msg.rotation.w or 1.0},
    }


_2D_TRAJECTORY_DEFAULTS = {
    "min_range": 0.0,
    "max_range": 30.0,
    "min_z": -0.8,
    "max_z": 2.0,
    "missing_data_ray_length": 5.0,
    "num_accumulated_range_data": 1,
    "voxel_filter_size": 0.025,
    "use_imu_data": False,
    "use_online_correlative_scan_matching": False,
    "imu_gravity_time_constant": 10.0,
    "adaptive_voxel_filter_options": {"max_length": 0.5, "min_num_points": 200, "max_range": 50.0},
    "loop_closure_adaptive_voxel_filter_options": {"max_length": 0.9, "min_num_points": 100, "max_range": 50.0},
    "ceres_scan_matcher_options": {
        "occupied_space_weight": 1.0,
        "translation_weight": 10.0,
        "rotation_weight": 40.0,
        "ceres_solver_options": {"use_nonmonotonic_steps": False, "max_num_iterations": 20, "num_threads": 1},
    },
    "motion_filter_options": {"max_time_seconds": 5.0, "max_distance_meters": 0.2, "max_angle_radians": 0.017453292519943295},
    "pose_extrapolator_options": {
        "use_imu_based": False,
        "constant_velocity": {"imu_gravity_time_constant": 10.0, "pose_queue_duration": 0.001},
    },
    "submaps_options": {
        "num_range_data": 90,
        "grid_options_2d": {"grid_type": "PROBABILITY_GRID", "resolution": 0.05},
        "range_data_inserter_options": {
            "range_data_inserter_type": "PROBABILITY_GRID_INSERTER_2D",
            "probability_grid_range_data_inserter_options_2d": {
                "insert_free_space": True,
                "hit_probability": 0.55,
                "miss_probability": 0.49,
            },
        },
    },
}


def _merge_options(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_options(merged[key], value)
        else:
            merged[key] = value
    return merged


def _trajectory_options_payload(options: dict) -> dict:
    payload = dict(options or {})
    renamed = {
        "trajectory_builder_2d": "trajectory_builder_2d_options",
        "trajectory_builder_3d": "trajectory_builder_3d_options",
    }
    for source, target in renamed.items():
        if source in payload and target not in payload:
            payload[target] = payload.pop(source)
    if isinstance(payload.get("trajectory_builder_2d_options"), dict):
        payload["trajectory_builder_2d_options"] = _merge_options(
            _2D_TRAJECTORY_DEFAULTS, payload["trajectory_builder_2d_options"],
        )
    return payload


def _fill_nested_trajectory_options(message, options: dict) -> None:
    from google.protobuf.json_format import ParseDict, ParseError

    try:
        ParseDict(_trajectory_options_payload(options), message, ignore_unknown_fields=True)
    except ParseError as exc:
        raise MappingError(
            "MAPPING_ENGINE_INCOMPATIBLE",
            "轨迹参数无法写入同事 proto",
            diagnostic=str(exc),
        ) from exc
    if not message.HasField("trajectory_builder_2d_options") and not message.HasField("trajectory_builder_3d_options"):
        raise MappingError("MAPPING_ENGINE_INCOMPATIBLE", "没有二维或三维轨迹参数")


def _fill_rigid(msg, rigid: dict) -> None:
    msg.translation.x = rigid["translation"]["x"]
    msg.translation.y = rigid["translation"]["y"]
    msg.translation.z = rigid["translation"]["z"]
    msg.rotation.x = rigid["rotation"]["x"]
    msg.rotation.y = rigid["rotation"]["y"]
    msg.rotation.z = rigid["rotation"]["z"]
    msg.rotation.w = rigid["rotation"]["w"]


class CartographerClient:
    def __init__(self, endpoint: str, proto_root, cache_root):
        self.endpoint = endpoint
        self.pb2, self.pb2_grpc = load_proto(proto_root, cache_root)
        self.channel = grpc.aio.insecure_channel(endpoint, options=_OPTIONS)
        self.stub = self.pb2_grpc.MapBuilderServiceStub(self.channel)
        self._sync_channel = grpc.insecure_channel(endpoint, options=_OPTIONS)
        self._sync_stub = self.pb2_grpc.MapBuilderServiceStub(self._sync_channel)
        self._textures: dict[tuple, tuple] = {}
        self._texture_bytes = 0

    async def close(self) -> None:
        self._sync_channel.close()
        await self.channel.close()

    async def add_trajectory(self, sensors: list[dict], trajectory_options: dict, client_id: str) -> int:
        req = self.pb2.AddTrajectoryRequest()
        for sensor in sensors:
            item = req.expected_sensor_ids.add()
            item.id = sensor["id"]
            item.type = _SENSOR_TYPE[sensor["type"]]
        req.client_id = client_id
        names = req.DESCRIPTOR.fields_by_name
        if "trajectory_builder_options_json" in names:
            req.trajectory_builder_options_json = json.dumps(trajectory_options)
        elif "trajectory_builder_options" in names:
            _fill_nested_trajectory_options(req.trajectory_builder_options, trajectory_options)
        else:
            raise MappingError("MAPPING_ENGINE_INCOMPATIBLE", "AddTrajectory 没有轨迹参数字段")
        try:
            resp = await self.stub.AddTrajectory(req, timeout=30)
        except grpc.aio.AioRpcError as exc:
            raise MappingError("MAPPING_TASK_START_FAILED", "启动轨迹失败", diagnostic=exc.details()) from exc
        if not isinstance(resp.trajectory_id, int):
            raise MappingError("MAPPING_TASK_START_FAILED", "引擎没有返回轨迹编号")
        return int(resp.trajectory_id)

    def _sensor_batch(self, items: list[dict]):
        req = self.pb2.AddSensorDataBatchRequest()
        for item in items:
            sensor = req.sensor_data.add()
            meta = item["sensor_metadata"]
            sensor.sensor_metadata.trajectory_id = int(meta["trajectory_id"])
            sensor.sensor_metadata.sensor_id = meta["sensor_id"]
            sensor.sensor_metadata.client_id = meta["client_id"]
            if "timed_point_cloud_data" in item:
                cloud = item["timed_point_cloud_data"]
                sensor.timed_point_cloud_data.timestamp = int(cloud["timestamp"])
                _fill_rigid_origin(sensor.timed_point_cloud_data.origin, cloud["origin"])
                for index, point in enumerate(cloud["point_data"]):
                    if index % 256 == 0:
                        time.sleep(0)
                    timed = sensor.timed_point_cloud_data.point_data.add()
                    timed.position.x = point["position"]["x"]
                    timed.position.y = point["position"]["y"]
                    timed.position.z = point["position"]["z"]
                    timed.time = point["time"]
            else:
                odom = item["odometry_data"]
                sensor.odometry_data.timestamp = int(odom["timestamp"])
                _fill_rigid(sensor.odometry_data.pose, odom["pose"])
        return req

    async def add_sensor_data_batch(self, items: list[dict]) -> None:
        try:
            await self.stub.AddSensorDataBatch(self._sensor_batch(items), timeout=30)
        except grpc.aio.AioRpcError as exc:
            raise MappingError("MAPPING_TASK_INTERRUPTED", "传感器数据发送失败", diagnostic=exc.details()) from exc

    def add_sensor_data_batch_blocking(self, items: list[dict]) -> None:
        try:
            self._sync_stub.AddSensorDataBatch(self._sensor_batch(items), timeout=30)
        except grpc.RpcError as exc:
            raise MappingError("MAPPING_TASK_INTERRUPTED", "传感器数据发送失败", diagnostic=exc.details()) from exc

    async def finish_trajectory(self, trajectory_id: int, client_id: str) -> None:
        req = self.pb2.FinishTrajectoryRequest(trajectory_id=trajectory_id, client_id=client_id)
        await self.stub.FinishTrajectory(req)

    async def run_final_optimization(self) -> None:
        request = _rpc_request(self.pb2, "RunFinalOptimizationRequest")
        try:
            await self.stub.RunFinalOptimization(request)
        except (grpc.aio.AioRpcError, AttributeError):
            return

    async def write_state_to_file(self, filename: str) -> None:
        resp = await self.stub.WriteStateToFile(self.pb2.WriteStateToFileRequest(filename=filename))
        if not resp.success:
            raise MappingError("MAPPING_RESULT_INVALID", "引擎没有写出自存档")

    async def delete_trajectory(self, trajectory_id: int, client_id: str) -> None:
        try:
            await self.stub.DeleteTrajectory(
                self.pb2.DeleteTrajectoryRequest(trajectory_id=trajectory_id, client_id=client_id),
                timeout=30,
            )
        except grpc.aio.AioRpcError:
            return

    async def incremental_protocol_version(self) -> int | None:
        request = _rpc_request(self.pb2, "GetIncrementalCapabilitiesRequest")
        try:
            resp = await self.stub.GetIncrementalCapabilities(request, timeout=30)
        except (grpc.aio.AioRpcError, AttributeError):
            return None
        version = getattr(resp, "protocol_version", None)
        return int(version) if version is not None else None

    def local_slam(self, trajectory_id: int):
        req = self.pb2.ReceiveLocalSlamResultsRequest(trajectory_id=trajectory_id)
        return self.stub.ReceiveLocalSlamResults(req)

    async def local_to_global(self, trajectory_id: int) -> dict:
        resp = await self.stub.GetLocalToGlobalTransform(
            self.pb2.GetLocalToGlobalTransformRequest(trajectory_id=trajectory_id),
            timeout=30,
        )
        return _rigid(resp.local_to_global)

    async def capture_grid(self, trajectory_id: int) -> dict | None:
        request = _rpc_request(self.pb2, "GetAllSubmapPosesRequest")
        try:
            poses = await self.stub.GetAllSubmapPoses(request, timeout=30)
        except (grpc.aio.AioRpcError, AttributeError):
            return None

        async def fetch(trajectory: int, index: int):
            return await self.stub.GetSubmap(_submap_request(self.pb2, trajectory, index), timeout=30)

        return await self._grid_from_poses(trajectory_id, poses, fetch)

    def capture_grid_blocking(self, trajectory_id: int) -> dict | None:
        """整图预览走同步通道，在独立线程里调用，不占用喂数的事件循环。"""
        request = _rpc_request(self.pb2, "GetAllSubmapPosesRequest")
        try:
            poses = self._sync_stub.GetAllSubmapPoses(request, timeout=30)
        except (grpc.RpcError, AttributeError):
            return None

        def fetch(trajectory: int, index: int):
            return self._sync_stub.GetSubmap(_submap_request(self.pb2, trajectory, index), timeout=30)

        return self._grid_from_poses_sync(trajectory_id, poses, fetch)

    async def _grid_from_poses(self, trajectory_id: int, poses, fetch) -> dict | None:
        submaps = []
        for pose in poses.submap_poses:
            if int(pose.submap_id.trajectory_id) != trajectory_id:
                continue
            key = (int(pose.submap_id.trajectory_id), int(pose.submap_id.submap_index))
            version = int(pose.submap_version)
            cached = self._textures.get(key)
            if cached is None or cached[0] != version:
                resp = await fetch(key[0], key[1])
                cached = self._remember_texture(key, version, resp)
                if cached is None:
                    continue
            submaps.append({"pose": _rigid(pose.global_pose), "texture": cached[1]})
        return _occupancy_grid(submaps)

    def _grid_from_poses_sync(self, trajectory_id: int, poses, fetch) -> dict | None:
        submaps = []
        for pose in poses.submap_poses:
            if int(pose.submap_id.trajectory_id) != trajectory_id:
                continue
            key = (int(pose.submap_id.trajectory_id), int(pose.submap_id.submap_index))
            version = int(pose.submap_version)
            cached = self._textures.get(key)
            if cached is None or cached[0] != version:
                cached = self._remember_texture(key, version, fetch(key[0], key[1]))
                if cached is None:
                    continue
            submaps.append({"pose": _rigid(pose.global_pose), "texture": cached[1]})
        return _occupancy_grid(submaps)

    def _remember_texture(self, key: tuple, version: int, resp):
        query = resp.submap_query_response
        if not query.textures:
            return None
        tex = query.textures[0]
        expected = int(tex.width) * int(tex.height) * 2
        try:
            cells = decode_texture(tex.cells, expected)
        except gzip.BadGzipFile:
            cells = tex.cells if len(tex.cells) == expected else None
        if cells is None:
            return None
        cached = (version, {
            "width": int(tex.width),
            "height": int(tex.height),
            "resolution": float(tex.resolution),
            "slicePose": _rigid(tex.slice_pose) if tex.slice_pose.rotation.w or tex.slice_pose.translation.x else identity(),
            "cells": cells,
        })
        self._textures[key] = cached
        self._texture_bytes += len(cells)
        if self._texture_bytes > MAX_TEXTURE_CACHE_BYTES:
            self._textures.clear()
            self._textures[key] = cached
            self._texture_bytes = len(cells)
        return cached


    async def load_frozen_state(self, filename: str) -> None:
        req = self.pb2.LoadStateFromFileRequest(file_path=filename, client_id="forkai", load_frozen_state=True)
        await self.stub.LoadStateFromFile(req, timeout=30)
        await self.run_final_optimization()

    async def select_submaps(self, corners: list[dict]) -> tuple[list[dict], int]:
        req = self.pb2.SelectSubmapsInRegionRequest(client_id="forkai")
        for corner in corners:
            point = req.corners.add()
            point.x, point.y, point.z = corner["x"], corner["y"], corner.get("z", 0)
        resp = await self.stub.SelectSubmapsInRegion(req, timeout=30)
        selected = [{"trajectoryId": int(item.trajectory_id), "submapIndex": int(item.submap_index)} for item in resp.submap_ids]
        return selected, int(resp.base_submap_count)

    async def trim_submaps(self, selected: list[dict]) -> None:
        req = self.pb2.TrimSubmapsRequest(client_id="forkai")
        for item in selected:
            sub = req.submap_ids.add()
            sub.trajectory_id = int(item["trajectoryId"])
            sub.submap_index = int(item["submapIndex"])
        await self.stub.TrimSubmaps(req, timeout=30)

    async def alignment_matched(self, trajectory_id: int) -> bool:
        request_cls = getattr(self.pb2, "GetIncrementalAlignmentStatusRequest", None)
        if request_cls is not None:
            request = request_cls(trajectory_id=trajectory_id)
        else:
            request = self.pb2.TrajectoryQueryRequest(trajectory_id=trajectory_id)
        resp = await self.stub.GetIncrementalAlignmentStatus(request, timeout=30)
        return bool(resp.has_historical_match)


def _occupancy_grid(submaps: list[dict]) -> dict | None:
    if not submaps:
        return None
    grid = compose_occupancy(submaps)
    grid["frameId"] = "map"
    return grid


def _rpc_request(pb2, request_name: str):
    request_cls = getattr(pb2, request_name, None)
    if request_cls is not None:
        return request_cls()
    from google.protobuf import empty_pb2
    return empty_pb2.Empty()


def _submap_request(pb2, trajectory_id: int, submap_index: int):
    request = pb2.GetSubmapRequest()
    request.submap_id.trajectory_id = trajectory_id
    request.submap_id.submap_index = submap_index
    return request


def _fill_rigid_origin(msg, origin: dict) -> None:
    msg.x = origin["x"]
    msg.y = origin["y"]
    msg.z = origin["z"]
