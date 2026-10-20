#!/usr/bin/env python3
"""按测试 proto 实现的假 Cartographer。只供 Mock，不代替同事的引擎。"""
import argparse
import asyncio
import gzip
import json
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

    async def AddTrajectory(self, request, context):
        self.trajectory_id += 1
        self.deleted = False
        return self.pb2.AddTrajectoryResponse(trajectory_id=self.trajectory_id)

    async def AddSensorDataBatch(self, request, context):
        self.batches += len(request.sensor_data)
        return self.pb2.AddSensorDataBatchResponse()

    async def FinishTrajectory(self, request, context):
        return self.pb2.FinishTrajectoryResponse()

    async def RunFinalOptimization(self, request, context):
        return self.pb2.RunFinalOptimizationResponse()

    async def WriteStateToFile(self, request, context):
        # 核对点 C：与 robospace 一样，在状态文件同目录写出 map.json。假引擎遵守这一点。
        path = Path(request.filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"MCAP-TEST")
        umcl = {
            "Header": "umcl-map",
            "MapRes": 0.05,
            "MinPose": "0 0",
            "MaxPose": "1 1",
            "NumPoints": 1,
            "ObsPoints": [[0, 0]],
        }
        (path.parent / "map.json").write_text(json.dumps(umcl), encoding="utf-8")
        return self.pb2.WriteStateToFileResponse(success=True)

    async def DeleteTrajectory(self, request, context):
        self.deleted = True
        return self.pb2.DeleteTrajectoryResponse()

    async def GetAllSubmapPoses(self, request, context):
        resp = self.pb2.GetAllSubmapPosesResponse()
        if self.trajectory_id and not self.deleted:
            pose = resp.submap_poses.add()
            pose.submap_id.trajectory_id = self.trajectory_id
            pose.submap_id.submap_index = 0
            pose.submap_version = 1
            pose.global_pose.rotation.w = 1
        return resp

    async def GetSubmap(self, request, context):
        resp = self.pb2.GetSubmapResponse()
        tex = resp.submap_query_response.textures.add()
        tex.width = 8
        tex.height = 8
        tex.resolution = 0.05
        tex.slice_pose.rotation.w = 1
        tex.cells = _texture()
        resp.submap_query_response.submap_version = 1
        return resp

    async def ReceiveLocalSlamResults(self, request, context):
        step = 0
        while not context.cancelled():
            await asyncio.sleep(0.2)
            step += 1
            msg = self.pb2.ReceiveLocalSlamResultsResponse()
            msg.timestamp = to_universal_ticks(time.time_ns())
            msg.local_pose.translation.x = step * 0.05
            msg.local_pose.rotation.w = 1
            point = msg.range_data.returns.add()
            point.position.x = 1.0
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
