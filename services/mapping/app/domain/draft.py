"""建图草稿。旧的 2D 在线请求（只有 mode=online）仍然接受。"""
from ..errors import MappingError


def _ranges(inputs: dict) -> tuple[list[dict], dict | None]:
    ranges = inputs.get("ranges")
    if not isinstance(ranges, list) or not ranges:
        raise MappingError("MAPPING_INPUT_INVALID", "至少需要一路激光")
    cleaned = []
    topics = set()
    for item in ranges:
        if not isinstance(item, dict):
            raise MappingError("MAPPING_INPUT_INVALID", "激光输入无效")
        sensor_id = str(item.get("sensorId") or "").strip()
        topic = str(item.get("topic") or "").strip()
        if not sensor_id or not topic or topic in topics:
            raise MappingError("MAPPING_INPUT_INVALID", "激光话题重复或为空")
        topics.add(topic)
        cleaned.append({"sensorId": sensor_id, "topic": topic})
    odom = inputs.get("odometry")
    odometry = None
    if odom not in (None, {}):
        if not isinstance(odom, dict) or not str(odom.get("topic") or "").strip():
            raise MappingError("MAPPING_INPUT_INVALID", "里程计话题无效")
        topic = str(odom["topic"]).strip()
        if topic in topics:
            raise MappingError("MAPPING_INPUT_INVALID", "里程计话题与激光重复")
        odometry = {"topic": topic}
    return cleaned, odometry


def _source(body: dict) -> dict:
    source = body.get("source")
    if isinstance(source, dict) and source.get("kind") in {"online", "recording"}:
        if source["kind"] == "recording" and not str(source.get("recordingId") or "").strip():
            raise MappingError("MAPPING_INPUT_INVALID", "缺少录包")
        return {"kind": source["kind"], **({"recordingId": source["recordingId"]} if source["kind"] == "recording" else {})}
    mode = body.get("mode") or "online"
    if mode == "online":
        return {"kind": "online"}
    raise MappingError("MAPPING_INPUT_INVALID", "数据来源无效")


def _objective(body: dict) -> dict:
    objective = body.get("objective") or {"kind": "create"}
    if not isinstance(objective, dict):
        raise MappingError("MAPPING_INPUT_INVALID", "建图目标无效")
    kind = objective.get("kind") or "create"
    if kind == "create":
        return {"kind": "create"}
    if kind not in {"extend", "update-region"}:
        raise MappingError("MAPPING_INPUT_INVALID", "建图目标无效")
    if not str(objective.get("baseProjectId") or "").strip():
        raise MappingError("MAPPING_INPUT_INVALID", "缺少基础地图")
    pose = objective.get("initialPose") or {}
    for key in ("xMm", "yMm", "yawRad"):
        if not isinstance(pose.get(key), (int, float)):
            raise MappingError("MAPPING_INPUT_INVALID", "初始位姿不完整")
    out = {"kind": kind, "baseProjectId": objective["baseProjectId"], "initialPose": pose}
    if kind == "update-region":
        region = objective.get("region") or {}
        if not all(isinstance(region.get(key), (int, float)) for key in ("minX", "minY", "maxX", "maxY")):
            raise MappingError("MAPPING_INPUT_INVALID", "更新范围不完整")
        if region["minX"] >= region["maxX"] or region["minY"] >= region["maxY"]:
            raise MappingError("MAPPING_INPUT_INVALID", "更新范围无效")
        out["region"] = region
    return out


def parse_draft(body: dict, capabilities: dict | None = None) -> dict:
    if not isinstance(body, dict):
        raise MappingError("MAPPING_INPUT_INVALID", "请求体无效")
    dimension = body.get("dimension")
    if dimension not in {"2d", "3d"}:
        raise MappingError("MAPPING_INPUT_INVALID", "维度无效")
    name = str(body.get("name") or "").strip()
    profile_id = str(body.get("profileId") or "").strip()
    if not name or len(name) > 64:
        raise MappingError("MAPPING_INPUT_INVALID", "地图名称需要 1 到 64 个字符")
    if not profile_id:
        raise MappingError("MAPPING_INPUT_INVALID", "缺少建图参数")
    source = _source(body)
    objective = _objective(body)
    inputs = body.get("inputs") if isinstance(body.get("inputs"), dict) else {}
    if dimension == "2d":
        ranges, odometry = _ranges(inputs)
        parsed_inputs = {"ranges": ranges, "odometry": odometry}
    else:
        lidar = inputs.get("lidar") or {}
        imu = inputs.get("imu") or {}
        if not str(lidar.get("topic") or "").strip() or not str(imu.get("topic") or "").strip():
            raise MappingError("MAPPING_INPUT_INVALID", "3D 需要雷达和 IMU 话题")
        parsed_inputs = {
            "lidar": {"sensorId": lidar.get("sensorId") or "lidar", "topic": lidar["topic"]},
            "imu": {"sensorId": imu.get("sensorId") or "imu", "topic": imu["topic"]},
        }
    mode = "online" if source["kind"] == "online" else "offline"
    if capabilities is not None:
        cap = (capabilities.get(dimension) or {})
        key = "online" if mode == "online" else "offline"
        if objective["kind"] != "create":
            key = "incremental"
        if not cap.get(key):
            raise MappingError("MAPPING_INPUT_INVALID", "这个建图方式还不可用")
    return {
        "dimension": dimension,
        "mode": mode,
        "name": name,
        "profileId": profile_id,
        "source": source,
        "objective": objective,
        "inputs": parsed_inputs,
    }
