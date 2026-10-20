"""按 proto 内容哈希缓存 grpc_tools 生成的代码。"""
import hashlib
import importlib
import sys
from pathlib import Path

from ..errors import MappingError

SERVICE_PROTO = Path("cartographer/cloud/proto/map_builder_service.proto")


def load_proto(proto_root: Path, cache_root: Path):
    proto_root = proto_root.resolve()
    service = proto_root / SERVICE_PROTO
    if not service.is_file():
        raise MappingError("MAPPING_ENGINE_UNAVAILABLE", f"缺少 {SERVICE_PROTO}")
    digest = hashlib.sha256()
    files = sorted(proto_root.rglob("*.proto"))
    for path in files:
        digest.update(path.relative_to(proto_root).as_posix().encode())
        digest.update(path.read_bytes())
    out = cache_root / digest.hexdigest()
    marker = out / ".ready"
    if not marker.is_file():
        out.mkdir(parents=True, exist_ok=True)
        from grpc_tools import protoc

        args = [
            "protoc",
            f"-I{proto_root}",
            f"--python_out={out}",
            f"--grpc_python_out={out}",
            *[str(path.relative_to(proto_root)) for path in files],
        ]
        if protoc.main(args) != 0:
            raise MappingError("MAPPING_ENGINE_UNAVAILABLE", "proto 编译失败")
        for folder in (out / "cartographer", out / "cartographer/cloud", out / "cartographer/cloud/proto"):
            init = folder / "__init__.py"
            if not init.is_file():
                init.write_text("", encoding="utf-8")
        marker.write_text("ok", encoding="utf-8")
    if str(out) not in sys.path:
        sys.path.insert(0, str(out))
    pb2 = importlib.import_module("cartographer.cloud.proto.map_builder_service_pb2")
    pb2_grpc = importlib.import_module("cartographer.cloud.proto.map_builder_service_pb2_grpc")
    return pb2, pb2_grpc
