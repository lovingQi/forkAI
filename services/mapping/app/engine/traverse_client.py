"""Traverse gRPC 客户端。字段以测试 proto 为准，核对点 F 未用同事 proto 关闭。"""
import json
from pathlib import Path

import grpc

from ..errors import MappingError
from .proto_loader import load_proto

_OPTIONS = [
    ("grpc.max_send_message_length", 64 * 1024 * 1024),
    ("grpc.max_receive_message_length", 64 * 1024 * 1024),
]


class TraverseClient:
    def __init__(self, endpoint: str, proto_root: Path, cache_root: Path):
        self.pb2, self.grpc_mod = load_proto(proto_root, cache_root, "traverse/engine/v1/traverse_engine.proto", "traverse.engine.v1.traverse_engine_pb2")
        self.channel = grpc.aio.insecure_channel(endpoint, options=_OPTIONS)
        self.stub = self.grpc_mod.TraverseMappingServiceStub(self.channel)
        self.session_id = 0

    async def close(self) -> None:
        await self.channel.close()

    async def server_info(self):
        return await self.stub.GetServerInfo(self.pb2.Empty(), timeout=30)

    def assert_compatible(self, info, mode: str, objective: str | None) -> None:
        if int(info.protocol_major) != 1 or int(info.protocol_minor) < 5:
            raise MappingError("MAPPING_ENGINE_INCOMPATIBLE", "Traverse 协议版本不兼容")
        if mode == "online" and not info.online:
            raise MappingError("MAPPING_ENGINE_INCOMPATIBLE", "引擎不支持在线 3D")
        if objective and int(info.protocol_minor) < 6:
            raise MappingError("MAPPING_ENGINE_INCOMPATIBLE", "引擎不支持 3D 增量")

    async def create(self, definition: dict) -> int:
        req = self.pb2.SessionRequest(definition_json=json.dumps(definition))
        valid = await self.stub.ValidateSession(req, timeout=30)
        if not valid.valid:
            raise MappingError("MAPPING_INPUT_INVALID", "3D 会话校验失败")
        resp = await self.stub.CreateSession(req, timeout=30)
        self.session_id = int(resp.session_id)
        if self.session_id <= 0:
            raise MappingError("MAPPING_TASK_START_FAILED", "没有拿到 3D 会话编号")
        return self.session_id

    async def add_batch(self, sequence: int, count: int) -> None:
        resp = await self.stub.AddSensorDataBatch(self.pb2.BatchRequest(
            session_id=self.session_id, batch_sequence=sequence, count=count,
        ), timeout=30)
        if int(resp.accepted_batch_sequence) != sequence or int(resp.accepted_sensor_messages) != count:
            raise MappingError("MAPPING_TASK_INTERRUPTED", "3D 批次没有被完整接收")

    async def finish(self) -> None:
        resp = await self.stub.FinishSession(self.pb2.SessionId(session_id=self.session_id), timeout=None)
        if not resp.accepted:
            raise MappingError("MAPPING_RESULT_INVALID", "结束 3D 会话失败")
        opt = await self.stub.RunFinalOptimization(self.pb2.SessionId(session_id=self.session_id), timeout=None)
        if not opt.success:
            raise MappingError("MAPPING_RESULT_INVALID", "3D 最终优化失败")

    async def export(self) -> None:
        await self.stub.ExportResult(self.pb2.SessionId(session_id=self.session_id), timeout=None)

    async def align(self, confirm: bool) -> str:
        resp = await self.stub.CheckIncrementalAlignment(self.pb2.AlignRequest(session_id=self.session_id, confirm=confirm), timeout=30)
        return resp.state

    async def snapshot(self, after: int = 0):
        return await self.stub.GetMapSnapshot(self.pb2.SnapshotRequest(session_id=self.session_id, max_points=50000, after_revision=after), timeout=30)

    async def delete(self) -> None:
        try:
            await self.stub.DeleteSession(self.pb2.SessionId(session_id=self.session_id), timeout=30)
        except grpc.aio.AioRpcError:
            return
