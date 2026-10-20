#!/usr/bin/env python3
"""测试用 Traverse。核对点 F 未关闭。"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import grpc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.engine.proto_loader import load_proto

PROTO = ROOT / "devtools" / "proto"


class Servicer:
    def __init__(self, pb2):
        self.pb2 = pb2
        self.session_id = 0
        self.total = 0
        self.workspace = Path(os.environ.get("FORKAI_FAKE_TRAVERSE_WORKSPACE", "/tmp"))
        self.fail_align = os.environ.get("FORKAI_FAKE_TRAVERSE_ALIGN") == "fail"

    def GetServerInfo(self, request, context):
        return self.pb2.ServerInfo(
            protocol_major=1, protocol_minor=6, engine_version=os.environ.get("FORKAI_FAKE_TRAVERSE_VERSION", "test"),
            build_id=os.environ.get("FORKAI_FAKE_TRAVERSE_BUILD", "test"), online=True, incremental=True,
        )

    def ValidateSession(self, request, context):
        json.loads(request.definition_json or "{}")
        return self.pb2.ValidateResponse(valid=True)

    def CreateSession(self, request, context):
        self.session_id += 1
        self.total = 0
        return self.pb2.SessionIdResponse(session_id=self.session_id)

    def AddSensorDataBatch(self, request, context):
        self.total += int(request.count)
        return self.pb2.BatchResponse(
            accepted_batch_sequence=request.batch_sequence,
            accepted_sensor_messages=request.count,
            total_accepted_sensor_messages=self.total,
        )

    def FinishSession(self, request, context):
        return self.pb2.Accepted(accepted=True)

    def RunFinalOptimization(self, request, context):
        return self.pb2.Success(success=True)

    def CheckIncrementalAlignment(self, request, context):
        if self.fail_align and not request.confirm:
            return self.pb2.AlignResponse(state="failed", message="高度不匹配")
        if request.confirm:
            return self.pb2.AlignResponse(state="confirmed", message="")
        return self.pb2.AlignResponse(state="ready" if self.total else "initializing", message="")

    def GetMapSnapshot(self, request, context):
        import struct
        points = struct.pack("<fffB", 1.0, 2.0, 0.5, 80) + b"\x00\x00\x00"
        return self.pb2.Snapshot(revision=1, points=points, point_count=1)

    def ExportResult(self, request, context):
        root = Path(os.environ.get("FORKAI_FAKE_TRAVERSE_WORKSPACE", "/tmp"))
        (root / "map_2d.json").write_text(json.dumps({
            "Header": "umcl-map", "MapRes": 0.05, "MinPose": "0 0", "MaxPose": "1 1", "NumPoints": 1, "ObsPoints": [[0, 0]],
        }), encoding="utf-8")
        (root / "global.pcd").write_text("VERSION 0.7\nFIELDS x y z\nSIZE 4 4 4\nTYPE F F F\nCOUNT 1 1 1\nWIDTH 1\nHEIGHT 1\nPOINTS 1\nDATA ascii\n0 0 0\n", encoding="utf-8")
        project = root / "map_project" / "v001"
        project.mkdir(parents=True, exist_ok=True)
        (project / "metadata.json").write_text(json.dumps({"map_id": "map-test"}), encoding="utf-8")
        (root / "map_project" / "current").write_text("v001\n", encoding="utf-8")
        return self.pb2.ExportResponse(session_id=request.session_id)

    def DeleteSession(self, request, context):
        return self.pb2.Deleted(deleted=True)

    def ReceiveLocalMappingResults(self, request, context):
        for revision in range(1, 3):
            yield self.pb2.LocalResult(revision=revision, point_count=1)


def serve():
    pb2, grpc_mod = load_proto(PROTO, ROOT / "data" / "proto_gen", "traverse/engine/v1/traverse_engine.proto", "traverse.engine.v1.traverse_engine_pb2")
    server = grpc.server(futures := __import__("concurrent.futures").futures.ThreadPoolExecutor(max_workers=4) or None)
    # 上面写法不稳，改用标准线程池。
    return pb2, grpc_mod


def main():
    import concurrent.futures
    argparse.ArgumentParser().parse_known_args()
    endpoint = os.environ.get("FORKAI_FAKE_ENGINE_ENDPOINT", "127.0.0.1:50052")
    pb2, grpc_mod = load_proto(PROTO, ROOT / "data" / "proto_gen", "traverse/engine/v1/traverse_engine.proto", "traverse.engine.v1.traverse_engine_pb2")
    server = grpc.server(concurrent.futures.ThreadPoolExecutor(max_workers=4))
    grpc_mod.add_TraverseMappingServiceServicer_to_server(Servicer(pb2), server)
    server.add_insecure_port(endpoint)
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    main()
