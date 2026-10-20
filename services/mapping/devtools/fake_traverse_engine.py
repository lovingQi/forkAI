#!/usr/bin/env python3
"""测试用 Traverse。核对点 F 未关闭。"""
import argparse
import json
import os
import struct
import sys
import threading
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
        self.revision = 0
        self.voxels = {}
        self.lock = threading.Lock()
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

    def _accumulate(self, data: bytes, step: int, count: int) -> None:
        step = step or 24
        for index in range(count):
            if (index + 1) * step > len(data):
                break
            x, y, z, intensity = struct.unpack_from("<fffB", data, index * step)
            key = (round(x / 0.1), round(y / 0.1), round(z / 0.1))
            self.voxels[key] = intensity

    def AddSensorDataBatch(self, request, context):
        messages = list(request.messages)
        accepted = len(messages) if messages else int(request.count)
        with self.lock:
            for msg in messages:
                if msg.kind == "point_cloud" and msg.point_count and msg.data:
                    self._accumulate(msg.data, msg.point_step, msg.point_count)
            self.total += accepted
            if accepted:
                self.revision += 1
            total = self.total
        return self.pb2.BatchResponse(
            accepted_batch_sequence=request.batch_sequence,
            accepted_sensor_messages=accepted,
            total_accepted_sensor_messages=total,
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
        limit = int(request.max_points or 30000)
        with self.lock:
            items = list(self.voxels.items())[:limit]
            revision = self.revision
        raw = bytearray(len(items) * 16)
        for index, ((ix, iy, iz), intensity) in enumerate(items):
            struct.pack_into("<ffff", raw, index * 16, ix * 0.1, iy * 0.1, iz * 0.1, float(intensity))
        return self.pb2.Snapshot(revision=revision, points=bytes(raw), point_count=len(items))

    def ExportResult(self, request, context):
        root = Path(os.environ.get("FORKAI_FAKE_TRAVERSE_WORKSPACE", "/tmp"))
        root.mkdir(parents=True, exist_ok=True)
        with self.lock:
            voxels = dict(self.voxels)
        columns = {}
        cloud = []
        for (ix, iy, iz), _intensity in voxels.items():
            x, y, z = ix * 0.1, iy * 0.1, iz * 0.1
            cloud.append((x, y, z))
            if 0.3 <= z <= 2.0:
                columns.setdefault(int(round(x * 1000)), []).append(int(round(y * 1000)))
        if not columns:
            columns = {0: [0]}
        obs = [[x, *ys] for x, ys in sorted(columns.items())]
        count = sum(len(column) - 1 for column in obs)
        xs = [column[0] for column in obs]
        ys = [value for column in obs for value in column[1:]]
        (root / "map_2d.json").write_text(json.dumps({
            "Header": "umcl-map",
            "MapRes": 0.05,
            "MinPose": f"{min(xs)} {min(ys)}",
            "MaxPose": f"{max(xs)} {max(ys)}",
            "NumPoints": count,
            "ObsPoints": obs,
        }), encoding="utf-8")
        if not cloud:
            cloud = [(0.0, 0.0, 0.0)]
        lines = [
            "VERSION 0.7", "FIELDS x y z", "SIZE 4 4 4", "TYPE F F F", "COUNT 1 1 1",
            f"WIDTH {len(cloud)}", "HEIGHT 1", f"POINTS {len(cloud)}", "DATA ascii",
        ]
        lines.extend(f"{x:.3f} {y:.3f} {z:.3f}" for x, y, z in cloud)
        (root / "global.pcd").write_text("\n".join(lines) + "\n", encoding="utf-8")
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
