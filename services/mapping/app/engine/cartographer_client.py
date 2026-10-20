"""Cartographer gRPC 客户端。

字段名按 robospace 客户端的 camelCase 转成 snake_case。
核对点 A/B：同事 proto 未到，本文件只在测试 proto 上验证过。
"""
import gzip
import json

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
        self._textures: dict[tuple, tuple] = {}
        self._texture_bytes = 0

    async def close(self) -> None:
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
            # 核对点 A：同事 proto 的轨迹参数是嵌套消息，这里只在字段名一致时填充。
            raise MappingError(
                "MAPPING_ENGINE_INCOMPATIBLE",
                "同事 proto 的轨迹参数字段还没有按测试 proto 适配",
                diagnostic="trajectory_builder_options",
            )
        else:
            raise MappingError("MAPPING_ENGINE_INCOMPATIBLE", "AddTrajectory 没有轨迹参数字段")
        try:
            resp = await self.stub.AddTrajectory(req, timeout=30)
        except grpc.aio.AioRpcError as exc:
            raise MappingError("MAPPING_TASK_START_FAILED", "启动轨迹失败", diagnostic=exc.details()) from exc
        if not isinstance(resp.trajectory_id, int):
            raise MappingError("MAPPING_TASK_START_FAILED", "引擎没有返回轨迹编号")
        return int(resp.trajectory_id)

    async def add_sensor_data_batch(self, items: list[dict]) -> None:
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
                for point in cloud["point_data"]:
                    timed = sensor.timed_point_cloud_data.point_data.add()
                    timed.position.x = point["position"]["x"]
                    timed.position.y = point["position"]["y"]
                    timed.position.z = point["position"]["z"]
                    timed.time = point["time"]
            else:
                odom = item["odometry_data"]
                sensor.odometry_data.timestamp = int(odom["timestamp"])
                _fill_rigid(sensor.odometry_data.pose, odom["pose"])
        try:
            await self.stub.AddSensorDataBatch(req, timeout=30)
        except grpc.aio.AioRpcError as exc:
            raise MappingError("MAPPING_TASK_INTERRUPTED", "传感器数据发送失败", diagnostic=exc.details()) from exc

    async def finish_trajectory(self, trajectory_id: int, client_id: str) -> None:
        req = self.pb2.FinishTrajectoryRequest(trajectory_id=trajectory_id, client_id=client_id)
        await self.stub.FinishTrajectory(req)

    async def run_final_optimization(self) -> None:
        await self.stub.RunFinalOptimization(self.pb2.RunFinalOptimizationRequest())

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
        try:
            resp = await self.stub.GetIncrementalCapabilities(self.pb2.GetIncrementalCapabilitiesRequest(), timeout=30)
        except grpc.aio.AioRpcError:
            return None
        return int(resp.protocol_version)

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
        poses = await self.stub.GetAllSubmapPoses(self.pb2.GetAllSubmapPosesRequest(), timeout=30)
        submaps = []
        for pose in poses.submap_poses:
            if int(pose.submap_id.trajectory_id) != trajectory_id:
                continue
            key = (int(pose.submap_id.trajectory_id), int(pose.submap_id.submap_index))
            version = int(pose.submap_version)
            cached = self._textures.get(key)
            if cached is None or cached[0] != version:
                resp = await self.stub.GetSubmap(
                    self.pb2.GetSubmapRequest(submap_id=self.pb2.SubmapId(
                        trajectory_id=key[0], submap_index=key[1],
                    )),
                    timeout=30,
                )
                query = resp.submap_query_response
                if not query.textures:
                    continue
                tex = query.textures[0]
                expected = int(tex.width) * int(tex.height) * 2
                try:
                    cells = decode_texture(tex.cells, expected)
                except gzip.BadGzipFile:
                    cells = tex.cells if len(tex.cells) == expected else None
                if cells is None:
                    continue
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
            submaps.append({"pose": _rigid(pose.global_pose), "texture": cached[1]})
        if not submaps:
            return None
        grid = compose_occupancy(submaps)
        grid["frameId"] = "map"
        return grid


def _fill_rigid_origin(msg, origin: dict) -> None:
    msg.x = origin["x"]
    msg.y = origin["y"]
    msg.z = origin["z"]
