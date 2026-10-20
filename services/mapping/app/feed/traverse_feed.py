"""在线 3D：订阅雷达和 IMU，按批发给 Traverse。"""
from ..errors import MappingError
from ..ingest.foxglove_client import FoxgloveClient
from ..ingest.livox import to_sensor_message
from ..ingest.observe import _channel_for
from ..ingest.ros1_codec import decode

_MAX_BATCH = 128
_MAX_BATCH_BYTES = 8 * 1024 * 1024


def _stamp_ns(decoded: dict) -> int:
    stamp = decoded["header"]["stamp"]
    return int(stamp["sec"]) * 1_000_000_000 + int(stamp.get("nsec") or 0)


def _chunks(messages: list[dict]) -> list[list[dict]]:
    groups = []
    current: list[dict] = []
    size = 0
    for item in messages:
        item_size = len(item.get("data") or b"")
        if current and (len(current) >= _MAX_BATCH or size + item_size > _MAX_BATCH_BYTES):
            groups.append(current)
            current = []
            size = 0
        current.append(item)
        size += item_size
    if current:
        groups.append(current)
    return groups


async def run_online_traverse(url: str, inputs: dict, client, scale: float, on_stats, stopped) -> str:
    lidar_topic = inputs["lidar"]["topic"]
    imu_topic = inputs["imu"]["topic"]
    specs = ({"topic": lidar_topic, "kind": "lidar", "sensorId": inputs["lidar"].get("sensorId") or "lidar"},
             {"topic": imu_topic, "kind": "imu", "sensorId": inputs["imu"].get("sensorId") or "imu"})
    fox = FoxgloveClient(url, max_message_bytes=16 * 1024 * 1024)
    await fox.connect(timeout=10)
    try:
        deadline_spins = 0
        while deadline_spins < 50 and any(_channel_for(fox, spec) is None for spec in specs):
            if stopped():
                return "stopped"
            await fox.next_message(0.2)
            deadline_spins += 1
        missing = [spec["topic"] for spec in specs if _channel_for(fox, spec) is None]
        if missing:
            raise MappingError("MAPPING_INPUT_INVALID", "缺少话题：" + "、".join(missing))
        await fox.subscribe([int(_channel_for(fox, spec)["id"]) for spec in specs])
        pending = []
        sequence = 0
        received = submitted = rejected = 0
        last: dict[str, int] = {}
        while not stopped():
            event = await fox.next_message(0.3)
            if stopped():
                return "stopped"
            if event is None:
                continue
            if event.get("kind") == "closed":
                return "bridge_disconnected"
            if event.get("kind") != "data":
                continue
            channel = fox.channels.get(event["channelId"])
            if not channel:
                continue
            spec = next((item for item in specs if item["topic"] == channel.get("topic")), None)
            if spec is None:
                continue
            decoded = decode(channel.get("schemaName") or "", event["payload"])
            stamp = _stamp_ns(decoded)
            topic = spec["topic"]
            if stamp <= 0:
                rejected += 1
                on_stats(received, submitted, rejected)
                continue
            if topic in last and stamp < last[topic]:
                raise MappingError("MAPPING_INPUT_INVALID", f"{topic} 时间回退")
            last[topic] = stamp
            message = to_sensor_message(decoded, stamp, spec["sensorId"], scale)
            if message is None:
                rejected += 1
                on_stats(received, submitted, rejected)
                continue
            received += 1
            if spec["kind"] == "imu":
                pending.append(message)
                if len(pending) < 120:
                    on_stats(received, submitted, rejected)
                    continue
                batch = pending
                pending = []
            else:
                batch = pending + [message]
                pending = []
            for chunk in _chunks(batch):
                sequence += 1
                await client.add_batch(sequence, chunk)
                submitted += len(chunk)
            on_stats(received, submitted, rejected)
        return "stopped"
    finally:
        await fox.close()
