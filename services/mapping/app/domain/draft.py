"""建图草稿。第一阶段只接受 2D 在线。"""
from ..errors import MappingError


def parse_draft(body: dict) -> dict:
    if not isinstance(body, dict):
        raise MappingError("MAPPING_INPUT_INVALID", "请求体无效")
    dimension = body.get("dimension")
    mode = body.get("mode")
    name = str(body.get("name") or "").strip()
    profile_id = str(body.get("profileId") or "").strip()
    inputs = body.get("inputs") if isinstance(body.get("inputs"), dict) else None
    if dimension != "2d" or mode != "online":
        raise MappingError("MAPPING_INPUT_INVALID", "当前只支持 2D 在线建图")
    if not name or len(name) > 64:
        raise MappingError("MAPPING_INPUT_INVALID", "地图名称需要 1 到 64 个字符")
    if not profile_id:
        raise MappingError("MAPPING_INPUT_INVALID", "缺少建图参数")
    ranges = (inputs or {}).get("ranges")
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
    odom = (inputs or {}).get("odometry")
    odometry = None
    if odom not in (None, {}):
        if not isinstance(odom, dict) or not str(odom.get("topic") or "").strip():
            raise MappingError("MAPPING_INPUT_INVALID", "里程计话题无效")
        topic = str(odom["topic"]).strip()
        if topic in topics:
            raise MappingError("MAPPING_INPUT_INVALID", "里程计话题与激光重复")
        odometry = {"topic": topic}
    return {
        "dimension": "2d",
        "mode": "online",
        "name": name,
        "profileId": profile_id,
        "inputs": {"ranges": cleaned, "odometry": odometry},
    }
