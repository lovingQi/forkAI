"""读取 profiles/<revision>.json，并按 robospace cartographerComposition 派生产品参数。"""
import json
from pathlib import Path

from ..errors import MappingError


def load_runtime_profile(root: Path, revision: str) -> dict:
    path = (root / f"{revision}.json").resolve()
    if root.resolve() not in path.parents:
        raise MappingError("MAPPING_INPUT_INVALID", "建图参数路径越界")
    if not path.is_file():
        raise MappingError("MAPPING_ENGINE_UNAVAILABLE", f"缺少运行参数 {revision}.json")
    profile = json.loads(path.read_text(encoding="utf-8"))
    if profile.get("revision") != revision:
        raise MappingError("MAPPING_ENGINE_UNAVAILABLE", "运行参数 revision 与文件名不一致")
    for key in ("expectedSensors", "trajectoryBuilderOptions", "range", "limits"):
        if key not in profile:
            raise MappingError("MAPPING_ENGINE_UNAVAILABLE", f"运行参数缺少 {key}")
    return profile


def _product(profile: dict, profile_id: str, name: str, pipeline_inputs: dict) -> dict:
    return {
        "id": profile_id,
        "name": name,
        "dimension": "2d",
        "pipeline": "lidar-2d",
        "runtimeRevision": profile["revision"],
        "inputs": pipeline_inputs,
        "trajectoryBuilderOptions": profile["trajectoryBuilderOptions"],
    }


def derive_product_profiles(root: Path) -> list[dict]:
    if not root.is_dir():
        return []
    out = []
    if (root / "1.json").is_file():
        base = load_runtime_profile(root, "1")
        topic = (base.get("range") or {}).get("topic") or "/scan"
        out.append(_product(base, "indoor-2d", "室内 2D", {
            "ranges": [{"sensorId": "range", "topic": topic, "required": True}],
            "odometry": None,
        }))
    if (root / "1-odom.json").is_file():
        odom = load_runtime_profile(root, "1-odom")
        range_topic = (odom.get("range") or {}).get("topic") or "/front/scan"
        odom_topic = (odom.get("odometry") or {}).get("topic") or "/odometry/filtered"
        out.append(_product(odom, "range-odom-2d", "激光 + 里程计 2D", {
            "ranges": [{"sensorId": "range", "topic": range_topic, "required": True}],
            "odometry": {"sensorId": "odometry", "topic": odom_topic, "required": True},
        }))
        out.append(_product(odom, "dual-range-odom-2d", "在线 2D（前后激光 + 里程计）", {
            "ranges": [
                {"sensorId": "front-laser", "topic": "/front/scan", "required": True},
                {"sensorId": "back-laser", "topic": "/back/scan", "required": True},
            ],
            "odometry": {"sensorId": "odometry", "topic": "/jarvis/odom", "required": True},
        }))
    return out


def product_by_id(profiles: list[dict], profile_id: str) -> dict:
    for item in profiles:
        if item["id"] == profile_id:
            return item
    raise MappingError("MAPPING_INPUT_INVALID", "建图参数不存在")
