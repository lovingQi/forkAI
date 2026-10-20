"""连接 Bridge，确认话题、类型、时间和坐标系。"""
import asyncio
import time

from ..errors import MappingError
from ..geom import identity
from ..robot.urdf import transform
from .foxglove_client import FoxgloveClient
from .ros1_codec import decode

LASER_SCHEMAS = {"sensor_msgs/LaserScan", "sensor_msgs/msg/LaserScan"}
ODOM_SCHEMAS = {"nav_msgs/Odometry", "nav_msgs/msg/Odometry"}
LIDAR_SCHEMAS = {"livox_ros_driver2/CustomMsg", "livox_ros_driver/CustomMsg"}
IMU_SCHEMAS = {"sensor_msgs/Imu", "sensor_msgs/msg/Imu"}
_SCHEMAS = {"range": LASER_SCHEMAS, "odometry": ODOM_SCHEMAS, "lidar": LIDAR_SCHEMAS, "imu": IMU_SCHEMAS}


def _stamp_ns(decoded: dict) -> int:
    stamp = decoded["header"]["stamp"]
    return int(stamp["sec"]) * 1_000_000_000 + int(stamp.get("nsec") or 0)


async def observe_topics(url: str, specs: list[dict], timeout: float, model: dict | None, tracking: str) -> dict:
    """specs: topic, kind(range|odometry), min_samples。返回每个话题的 frame / child。"""
    client = FoxgloveClient(url)
    await client.connect(timeout=min(10, timeout))
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline and not _all_advertised(client, specs):
            if client.closed_reason == "bad_encoding":
                raise MappingError("MAPPING_INPUT_INVALID", "Bridge 使用了 ROS1 以外的编码")
            await client.next_message(0.2)
        missing = [spec["topic"] for spec in specs if not _channel_for(client, spec)]
        if missing:
            raise MappingError("MAPPING_INPUT_INVALID", "缺少话题：" + "、".join(missing))
        ids = []
        for spec in specs:
            channel = _channel_for(client, spec)
            if channel.get("encoding") not in (None, "ros1"):
                raise MappingError("MAPPING_INPUT_INVALID", f"{spec['topic']} 不是 ROS1 编码")
            ids.append(int(channel["id"]))
        await client.subscribe(ids)
        got: dict[str, list] = {spec["topic"]: [] for spec in specs}
        last: dict[str, int] = {}
        while time.monotonic() < deadline and any(len(v) < 3 for v in got.values()):
            event = await client.next_message(0.3)
            if not event or event.get("kind") != "data":
                continue
            channel = client.channels.get(event["channelId"])
            if not channel:
                continue
            topic = channel["topic"]
            if topic not in got:
                continue
            decoded = decode(channel.get("schemaName") or "", event["payload"])
            stamp = _stamp_ns(decoded)
            if stamp <= 0:
                raise MappingError("MAPPING_INPUT_INVALID", f"{topic} 时间戳为 0")
            if topic in last and stamp < last[topic]:
                raise MappingError("MAPPING_INPUT_INVALID", f"{topic} 时间回退")
            last[topic] = stamp
            frame = str(decoded["header"]["frame_id"]).lstrip("/")
            child = str(decoded.get("child_frame_id") or "").lstrip("/")
            spec = next(item for item in specs if item["topic"] == topic)
            if model is not None and spec["kind"] == "range":
                transform(model, tracking, frame)
            got[topic].append({"frame": frame, "child": child, "stamp": stamp})
        short = [topic for topic, rows in got.items() if len(rows) < 3]
        if short:
            raise MappingError("MAPPING_INPUT_INVALID", "样本不足：" + "、".join(short))
        return {topic: rows[-1] for topic, rows in got.items()}
    finally:
        await client.close()


def _channel_for(client: FoxgloveClient, spec: dict):
    allowed = _SCHEMAS[spec["kind"]]
    for channel in client.channels.values():
        if channel.get("topic") == spec["topic"] and channel.get("schemaName") in allowed:
            return channel
    return None


def _all_advertised(client: FoxgloveClient, specs: list[dict]) -> bool:
    return all(_channel_for(client, spec) for spec in specs)


def extrinsic_for(model: dict, tracking: str, frame: str) -> dict:
    if not frame:
        return identity()
    return transform(model, tracking, frame)
