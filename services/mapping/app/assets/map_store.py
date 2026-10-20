"""地图工程目录。ZIP 只放 robospace 能导入的两份产物。

磁盘上另存 mapping-profile.json、online-input.json、robot.urdf、meta.json，
不写入 ZIP：robospace isSupportedImportedProject 的 2D 工程只接受
navigation-map-2d + engine-state，并且拒绝未在 representations 中声明的文件。
"""
import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..errors import MappingError
from ..preview.png import encode_gray8
from .umcl import is_umcl_json_map


def umcl_preview_png(umcl: dict) -> bytes:
    xs = []
    ys = []
    for column in umcl.get("ObsPoints") or []:
        if not isinstance(column, list) or len(column) < 2:
            continue
        for y in column[1:]:
            xs.append(float(column[0]))
            ys.append(float(y))
    side = 128
    pixels = bytearray([255]) * (side * side)
    if xs:
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        span = max(max_x - min_x, max_y - min_y, 1.0)
        scale = (side - 1) / span
        for x, y in zip(xs, ys):
            column = min(side - 1, max(0, int((x - min_x) * scale)))
            row = min(side - 1, max(0, int((max_y - y) * scale)))
            pixels[row * side + column] = 100
    return encode_gray8(side, side, bytes(pixels))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_tree(path: Path) -> None:
    for root, dirs, files in os.walk(path):
        for name in files:
            fd = os.open(os.path.join(root, name), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        dirfd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)


def project_manifest(project_id: str, name: str, robot_resource_id: str, endpoint: str, stop_reason: str | None, objective: dict | None = None, parent: dict | None = None) -> dict:
    project = {
        "schemaVersion": 1,
        "projectId": project_id,
        "name": name,
        "representations": [
            {"role": "navigation-map-2d", "kind": "localization-map-2d", "path": "map.json"},
            {"role": "engine-state", "kind": "engine-state", "path": "mapping_state.mcap"},
        ],
        "map2dFromLocalization": (parent or {}).get("map2dFromLocalization") or {"txMm": 0, "tyMm": 0, "yawRad": 0},
    }
    if endpoint:
        online = {"robotResourceId": robot_resource_id, "endpoint": endpoint}
        if stop_reason:
            online["sourceStopReason"] = stop_reason
        project["onlineSource"] = online
    if objective and objective.get("kind") not in (None, "create"):
        project["incremental"] = {
            "schemaVersion": 1,
            "parentProjectId": objective["baseProjectId"],
            "parentStateSha256": (parent or {}).get("stateSha256") or "",
            "objective": objective,
        }
    return project


def validate_project(project: dict) -> None:
    reps = project.get("representations") or []
    roles = {(item.get("role"), item.get("kind")) for item in reps}
    if ("navigation-map-2d", "localization-map-2d") not in roles:
        raise MappingError("MAPPING_RESULT_INVALID", "工程缺少二维导航图")
    if ("engine-state", "engine-state") not in roles:
        raise MappingError("MAPPING_RESULT_INVALID", "工程缺少引擎状态")
    if any(item.get("role") == "localization-map-3d" for item in reps):
        if len(reps) not in (3, 4):
            raise MappingError("MAPPING_RESULT_INVALID", "三维工程表示数量不符合导入规则")
        return
    if len(reps) != 2:
        raise MappingError("MAPPING_RESULT_INVALID", "二维工程表示数量不符合 robospace 导入规则")
    online = project.get("onlineSource")
    if online is not None:
        import re
        endpoint = online.get("endpoint") or ""
        if not re.fullmatch(r"ws://(?:\d{1,3}\.){3}\d{1,3}:8765/?", endpoint):
            raise MappingError("MAPPING_RESULT_INVALID", "在线来源地址不符合工程格式")


class MapStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def commit(self, task: dict, staging: Path, endpoint: str, robot_resource_id: str, preview: dict | None) -> str:
        map_json = staging / "map.json"
        state = staging / "mapping_state.mcap"
        if not map_json.is_file() or not state.is_file():
            raise MappingError("MAPPING_RESULT_INVALID", "引擎没有写出地图文件")
        try:
            parsed = json.loads(map_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise MappingError("MAPPING_RESULT_INVALID", "map.json 不是合法 JSON") from exc
        if not is_umcl_json_map(parsed):
            raise MappingError("MAPPING_RESULT_INVALID", "map.json 不是合法 UMCL 地图")
        project_id = str(uuid.uuid4())
        objective = task.get("objective") or {"kind": "create"}
        parent = None
        if objective.get("kind") not in (None, "create"):
            parent_dir = self._dir(objective["baseProjectId"])
            parent = json.loads((parent_dir / "project.json").read_text(encoding="utf-8"))
            parent["stateSha256"] = _sha256(parent_dir / "mapping_state.mcap")
        project = project_manifest(project_id, task["name"], robot_resource_id, endpoint, task.get("stopReason"), objective, parent)
        validate_project(project)
        incoming = self.root / f".incoming-{project_id}"
        final = self.root / project_id
        if incoming.exists():
            shutil.rmtree(incoming)
        incoming.mkdir(parents=True)
        for name in ("map.json", "mapping_state.mcap", "mapping-profile.json", "online-input.json", "robot.urdf"):
            src = staging / name
            if src.is_file():
                shutil.copy2(src, incoming / name)
        (incoming / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
        if preview and preview.get("cells"):
            pixels = bytes(255 if cell < 0 else int(cell) for cell in preview["cells"])
            (incoming / "preview.png").write_bytes(encode_gray8(preview["width"], preview["height"], pixels))
        meta = {
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "taskId": task["id"],
            "engineVersion": (task.get("snapshot") or {}).get("engineVersion"),
            "buildId": (task.get("snapshot") or {}).get("buildId"),
            "files": {
                name: _sha256(incoming / name)
                for name in os.listdir(incoming)
                if (incoming / name).is_file()
            },
        }
        (incoming / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        _fsync_tree(incoming)
        os.rename(incoming, final)
        return project_id

    def list_maps(self) -> list[dict]:
        rows = []
        for child in self.root.iterdir() if self.root.is_dir() else []:
            project_path = child / "project.json"
            if child.name.startswith(".") or not project_path.is_file():
                continue
            project = json.loads(project_path.read_text(encoding="utf-8"))
            meta_path = child / "meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
            dimension = meta.get("dimension")
            if dimension not in {"2d", "3d"}:
                roles = {item.get("role") for item in project.get("representations") or []}
                dimension = "3d" if "localization-map-3d" in roles else "2d"
            rows.append({
                "projectId": project["projectId"],
                "name": project["name"],
                "createdAt": meta.get("createdAt"),
                "engineVersion": meta.get("engineVersion"),
                "dimension": dimension,
            })
        rows.sort(key=lambda item: item.get("createdAt") or "", reverse=True)
        return rows

    def get(self, project_id: str) -> dict:
        path = self._dir(project_id)
        project = json.loads((path / "project.json").read_text(encoding="utf-8"))
        meta_path = path / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        return {"project": project, "meta": meta}

    def delete(self, project_id: str) -> None:
        shutil.rmtree(self._dir(project_id))

    def preview_png(self, project_id: str) -> bytes:
        path = self._dir(project_id) / "preview.png"
        if not path.is_file():
            raise MappingError("MAPPING_INPUT_INVALID", "这张地图没有预览图", status=404)
        return path.read_bytes()

    def commit_3d(self, task: dict, staging: Path, endpoint: str, robot_resource_id: str) -> str:
        project_id = str(uuid.uuid4())
        project = {
            "schemaVersion": 1,
            "projectId": project_id,
            "name": task["name"],
            "representations": [
                {"role": "navigation-map-2d", "kind": "localization-map-2d", "path": "map_2d.json"},
                {"role": "localization-map-3d", "kind": "point-cloud", "path": "global.pcd"},
                {"role": "engine-state", "kind": "engine-state", "path": "map_project"},
            ],
            "map2dFromLocalization": {"txMm": 0, "tyMm": 0, "yawRad": 0},
        }
        if endpoint:
            project["onlineSource"] = {"robotResourceId": robot_resource_id, "endpoint": endpoint}
        validate_project(project)
        try:
            umcl = json.loads((staging / "map_2d.json").read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise MappingError("MAPPING_RESULT_INVALID", "map_2d.json 不是合法 JSON") from exc
        if not is_umcl_json_map(umcl):
            raise MappingError("MAPPING_RESULT_INVALID", "map_2d.json 不是合法 UMCL 地图")
        final = self.root / project_id
        incoming = self.root / f".incoming-{project_id}"
        if incoming.exists():
            shutil.rmtree(incoming)
        incoming.mkdir()
        for name in ("map_2d.json", "global.pcd"):
            shutil.copy2(staging / name, incoming / name)
        shutil.copytree(staging / "map_project", incoming / "map_project")
        (incoming / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
        (incoming / "preview.png").write_bytes(umcl_preview_png(umcl))
        meta = {
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "taskId": task["id"],
            "engineVersion": (task.get("snapshot") or {}).get("engineVersion"),
            "buildId": (task.get("snapshot") or {}).get("buildId"),
            "dimension": "3d",
        }
        (incoming / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        _fsync_tree(incoming)
        os.rename(incoming, final)
        return project_id

    def directory(self, project_id: str) -> Path:
        return self._dir(project_id)

    def _dir(self, project_id: str) -> Path:
        if not project_id or "/" in project_id or project_id.startswith("."):
            raise MappingError("MAPPING_INPUT_INVALID", "地图不存在", status=404)
        path = (self.root / project_id).resolve()
        if path.parent != self.root.resolve() or not path.is_dir():
            raise MappingError("MAPPING_INPUT_INVALID", "地图不存在", status=404)
        return path
