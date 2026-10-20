"""按记录时间读取 MCAP 或 ROS1 bag。"""
from pathlib import Path

from ..errors import MappingError
from ..ingest.ros1_codec import decode


def _iter_mcap(path: Path, topics: set[str]):
    from mcap.reader import make_reader

    with open(path, "rb") as handle:
        reader = make_reader(handle)
        summary = reader.get_summary()
    count = 0
    start = end = None
    if summary and summary.statistics:
        count = int(summary.statistics.message_count)
        start = int(summary.statistics.message_start_time)
        end = int(summary.statistics.message_end_time)

    def messages():
        with open(path, "rb") as handle:
            reader = make_reader(handle)
            for schema, channel, message in reader.iter_messages(topics=list(topics) if topics else None):
                topic = channel.topic
                if topics and topic not in topics:
                    continue
                payload = bytes(message.data)
                decoded = decode(schema.name if schema else "", payload)
                stamp = int(decoded["header"]["stamp"]["sec"]) * 1_000_000_000 + int(decoded["header"]["stamp"].get("nsec") or 0)
                yield {
                    "topic": topic,
                    "stamp_ns": stamp or int(message.log_time),
                    "frame": str(decoded["header"].get("frame_id") or ""),
                    "decoded": decoded,
                    "size": len(payload),
                }

    return {"count": count, "startTimeNs": start, "endTimeNs": end}, messages


def _iter_bag(path: Path, topics: set[str]):
    from rosbags.rosbag1 import Reader

    bag = Reader(path)
    bag.open()
    start = int(bag.start_time) if bag.start_time else None
    end = int(bag.end_time) if bag.end_time else None
    count = sum(conn.msgcount for conn in bag.connections if not topics or conn.topic in topics)

    def messages():
        try:
            for conn, timestamp, raw in bag.messages():
                if topics and conn.topic not in topics:
                    continue
                decoded = decode(conn.msgtype, raw)
                stamp = int(decoded["header"]["stamp"]["sec"]) * 1_000_000_000 + int(decoded["header"]["stamp"].get("nsec") or 0)
                yield {
                    "topic": conn.topic,
                    "stamp_ns": stamp or int(timestamp),
                    "frame": str(decoded["header"].get("frame_id") or ""),
                    "decoded": decoded,
                    "size": len(raw),
                }
        finally:
            bag.close()

    return {"count": count, "startTimeNs": start, "endTimeNs": end}, messages


def open_recording(path: str, topics: list[str] | None = None):
    file_path = Path(path)
    if not file_path.is_file():
        raise MappingError("MAPPING_INPUT_INVALID", "录包不存在")
    selected = set(topics or [])
    suffix = file_path.suffix.lower()
    if suffix == ".mcap":
        return _iter_mcap(file_path, selected)
    if suffix == ".bag":
        return _iter_bag(file_path, selected)
    raise MappingError("MAPPING_INPUT_INVALID", "只支持 .mcap 和 ROS1 .bag")
