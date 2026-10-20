#!/usr/bin/env python3
"""按测试 proto 实现的假 Cartographer。只供 Mock，不代替同事的引擎。"""
import argparse
import asyncio
import gzip
import json
import math
import os
import sys
import time
from pathlib import Path

import grpc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.engine.proto_loader import load_proto
from app.geom import to_universal_ticks

PROTO = ROOT / "devtools" / "proto"
RES = 0.05


def _yaw(rotation) -> float:
    x, y, z, w = rotation.x, rotation.y, rotation.z, rotation.w or 1.0
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def _world(tx: float, ty: float, yaw: float, x: float, y: float) -> tuple[float, float]:
    cosine, sine = math.cos(yaw), math.sin(yaw)
    return cosine * x - sine * y + tx, sine * x + cosine * y + ty


class _Grid:
    def __init__(self):
        self.occ: set[tuple[int, int]] = set()
        self.free: set[tuple[int, int]] = set()
        self.version = 1

    def mark_occupied(self, x: float, y: float) -> None:
        key = (math.floor(x / RES), math.floor(y / RES))
        self.occ.add(key)
        self.free.discard(key)

    def mark_free(self, x: float, y: float) -> None:
        key = (math.floor(x / RES), math.floor(y / RES))
        if key not in self.occ:
            self.free.add(key)

    def texture(self):
        if not self.occ:
            return None
        ixs = [key[0] for key in self.occ]
        iys = [key[1] for key in self.occ]
        min_i, max_i = min(ixs), max(ixs)
        min_j, max_j = min(iys), max(iys)
        width = min(max_i - min_i + 1, 700)
        height = min(max_j - min_j + 1, 700)
        raw = bytearray(width * height * 2)
        for ix, iy in self.free:
            column, row = ix - min_i, iy - min_j
            if 0 <= column < width and 0 <= row < height:
                index = (row * width + column) * 2
                raw[index] = 255
                raw[index + 1] = 255
        for ix, iy in self.occ:
            column, row = ix - min_i, iy - min_j
            if 0 <= column < width and 0 <= row < height:
                index = (row * width + column) * 2
                raw[index] = 0
                raw[index + 1] = 255
        return width, height, min_i * RES, min_j * RES, gzip.compress(bytes(raw))


def _texture() -> bytes:
    width, height = 8, 8
    raw = bytearray(width * height * 2)
    for row in range(height):
        index = (row * width + 2) * 2
        raw[index] = 0
        raw[index + 1] = 255
    return gzip.compress(bytes(raw))


class Servicer:
    def __init__(self, pb2):
        self.pb2 = pb2
        self.trajectory_id = 0
        self.batches = 0
        self.deleted = False
        self.tx = 0.0
        self.ty = 0.0
        self.yaw = 0.0
        self.laser_frames = 0
        self.submaps: list[_Grid] = []
        self.last_returns: list[tuple[float, float]] = []

    async def AddTrajectory(self, request, context):
        self.trajectory_id += 1
        self.deleted = False
        return self.pb2.AddTrajectoryResponse(trajectory_id=self.trajectory_id)

    async def AddSensorDataBatch(self, request, context):
        self.batches += len(request.sensor_data)
        tx, ty, yaw = self.tx, self.ty, self.yaw
        for sensor in request.sensor_data:
            if sensor.HasField("odometry_data"):
                pose = sensor.odometry_data.pose
                self.tx = pose.translation.x
                self.ty = pose.translation.y
                self.yaw = _yaw(pose.rotation)
                tx, ty, yaw = self.tx, self.ty, self.yaw
                continue
            if not sensor.HasField("timed_point_cloud_data"):
                continue
            cloud = sensor.timed_point_cloud_data
            self.laser_frames += 1
            if not self.submaps or (self.laser_frames > 1 and (self.laser_frames - 1) % 90 == 0):
                self.submaps.append(_Grid())
            grid = self.submaps[-1]
            origin_x, origin_y = _world(tx, ty, yaw, cloud.origin.x, cloud.origin.y)
            returns = []
            for point in cloud.point_data:
                world_x, world_y = _world(tx, ty, yaw, point.position.x, point.position.y)
                grid.mark_free(origin_x + (world_x - origin_x) * 0.5, origin_y + (world_y - origin_y) * 0.5)
                grid.mark_occupied(world_x, world_y)
                if len(returns) < 400:
                    returns.append((world_x, world_y))
            self.last_returns = returns
            grid.version += 1
        return self.pb2.AddSensorDataBatchResponse()

    async def FinishTrajectory(self, request, context):
        return self.pb2.FinishTrajectoryResponse()

    async def RunFinalOptimization(self, request, context):
        return self.pb2.RunFinalOptimizationResponse()

    def _umcl(self) -> dict:
        columns: dict[int, list[int]] = {}
        for grid in self.submaps:
            for ix, iy in grid.occ:
                columns.setdefault(int(round(ix * RES * 1000)), []).append(int(round(iy * RES * 1000)))
        if not columns:
            return {
                "Header": "umcl-map", "MapRes": 0.05, "MinPose": "0 0", "MaxPose": "1 1",
                "NumPoints": 1, "ObsPoints": [[0, 0]],
            }
        obs = [[x, *ys] for x, ys in sorted(columns.items())]
        count = sum(len(column) - 1 for column in obs)
        xs = [column[0] for column in obs]
        ys = [value for column in obs for value in column[1:]]
        return {
            "Header": "umcl-map",
            "MapRes": RES,
            "MinPose": f"{min(xs)} {min(ys)}",
            "MaxPose": f"{max(xs)} {max(ys)}",
            "NumPoints": count,
            "ObsPoints": obs,
        }

    async def WriteStateToFile(self, request, context):
        # 核对点 C：与 robospace 一样，在状态文件同目录写出 map.json。假引擎遵守这一点。
        path = Path(request.filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"MCAP-TEST")
        (path.parent / "map.json").write_text(json.dumps(self._umcl()), encoding="utf-8")
        return self.pb2.WriteStateToFileResponse(success=True)

    async def DeleteTrajectory(self, request, context):
        self.deleted = True
        return self.pb2.DeleteTrajectoryResponse()

    async def GetAllSubmapPoses(self, request, context):
        resp = self.pb2.GetAllSubmapPosesResponse()
        if self.trajectory_id and not self.deleted:
            for index, grid in enumerate(self.submaps):
                if not grid.occ:
                    continue
                pose = resp.submap_poses.add()
                pose.submap_id.trajectory_id = self.trajectory_id
                pose.submap_id.submap_index = index
                pose.submap_version = grid.version
                pose.global_pose.rotation.w = 1
        return resp

    async def GetSubmap(self, request, context):
        resp = self.pb2.GetSubmapResponse()
        index = int(request.submap_id.submap_index)
        grid = self.submaps[index] if 0 <= index < len(self.submaps) else None
        painted = grid.texture() if grid else None
        tex = resp.submap_query_response.textures.add()
        if painted is None:
            tex.width = 8
            tex.height = 8
            tex.resolution = RES
            tex.slice_pose.rotation.w = 1
            tex.cells = _texture()
            resp.submap_query_response.submap_version = 1
            return resp
        width, height, origin_x, origin_y, cells = painted
        tex.width = width
        tex.height = height
        tex.resolution = RES
        tex.slice_pose.translation.x = origin_x
        tex.slice_pose.translation.y = -origin_y
        tex.slice_pose.rotation.z = math.sin(math.pi / 4)
        tex.slice_pose.rotation.w = math.cos(math.pi / 4)
        tex.cells = cells
        resp.submap_query_response.submap_version = grid.version
        return resp

    async def ReceiveLocalSlamResults(self, request, context):
        while not context.cancelled():
            await asyncio.sleep(0.2)
            msg = self.pb2.ReceiveLocalSlamResultsResponse()
            msg.timestamp = to_universal_ticks(time.time_ns())
            msg.local_pose.translation.x = self.tx
            msg.local_pose.translation.y = self.ty
            msg.local_pose.rotation.z = math.sin(self.yaw / 2)
            msg.local_pose.rotation.w = math.cos(self.yaw / 2)
            for x, y in self.last_returns[:2000]:
                point = msg.range_data.returns.add()
                point.position.x = x
                point.position.y = y
            if not self.last_returns:
                point = msg.range_data.returns.add()
                point.position.x = self.tx
                point.position.y = self.ty
            yield msg

    async def GetLocalToGlobalTransform(self, request, context):
        resp = self.pb2.GetLocalToGlobalTransformResponse()
        resp.local_to_global.rotation.w = 1
        return resp

    async def GetIncrementalCapabilities(self, request, context):
        return self.pb2.GetIncrementalCapabilitiesResponse(protocol_version=1, lightweight_graph_queries=True)

    async def LoadStateFromFile(self, request, context):
        return self.pb2.LoadStateFromFileResponse(trajectory_count=1)

    async def SelectSubmapsInRegion(self, request, context):
        resp = self.pb2.SelectSubmapsInRegionResponse(base_submap_count=4)
        xs = [corner.x for corner in request.corners]
        ys = [corner.y for corner in request.corners]
        if xs and max(xs) - min(xs) < 0.05 and max(ys) - min(ys) < 0.05:
            return resp
        if xs and max(xs) - min(xs) > 1000:
            for index in range(4):
                item = resp.submap_ids.add()
                item.trajectory_id = 0
                item.submap_index = index
            return resp
        item = resp.submap_ids.add()
        item.trajectory_id = 0
        item.submap_index = 1
        return resp

    async def TrimSubmaps(self, request, context):
        return self.pb2.TrimSubmapsResponse()

    async def GetTrajectoryNodePoses(self, request, context):
        resp = self.pb2.GetTrajectoryNodePosesResponse()
        node = resp.node_poses.add()
        node.node_id.trajectory_id = 0
        node.node_id.submap_index = 0
        node.global_pose.rotation.w = 1
        node.timestamp = 1
        return resp

    async def GetConstraints(self, request, context):
        return self.pb2.GetConstraintsResponse()

    async def GetRetainedMapGeometry(self, request, context):
        return self.pb2.GetRetainedMapGeometryResponse(submap_count=3)

    async def GetRetainedMap(self, request, context):
        return self.pb2.GetRetainedMapResponse(width=4, height=4)

    async def GetIncrementalAlignmentStatus(self, request, context):
        return self.pb2.GetIncrementalAlignmentStatusResponse(has_historical_match=self.batches > 0)


async def serve(endpoint: str) -> None:
    pb2, pb2_grpc = load_proto(PROTO, ROOT / "data" / "proto_gen")
    server = grpc.aio.server(options=[
        ("grpc.max_send_message_length", 64 * 1024 * 1024),
        ("grpc.max_receive_message_length", 64 * 1024 * 1024),
    ])
    pb2_grpc.add_MapBuilderServiceServicer_to_server(Servicer(pb2), server)
    server.add_insecure_port(endpoint)
    await server.start()
    await server.wait_for_termination()


def main() -> None:
    argparse.ArgumentParser().parse_known_args()
    endpoint = os.environ.get("FORKAI_FAKE_ENGINE_ENDPOINT", "127.0.0.1:50051")
    asyncio.run(serve(endpoint))


if __name__ == "__main__":
    main()
