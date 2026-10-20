"""从 Foxglove Bridge 把选定话题录成 MCAP。"""
import shutil
import time
from pathlib import Path

from mcap.writer import Writer

from ..errors import MappingError
from ..ingest.foxglove_client import FoxgloveClient


class BridgeRecorder:
    def __init__(self, meta: dict, url: str, max_bytes: int, min_free: int, max_message_bytes: int):
        self.meta = meta
        self.url = url
        self.max_bytes = max_bytes
        self.min_free = min_free
        self.max_message_bytes = max_message_bytes
        self.stop_reason = None

    def stop(self, reason: str = "stopped") -> None:
        if self.stop_reason is None:
            self.stop_reason = reason

    async def run(self) -> dict:
        path = Path(self.meta["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        client = FoxgloveClient(self.url, max_message_bytes=self.max_message_bytes)
        await client.connect()
        wanted = {item["topic"]: item for item in self.meta["topics"]}
        deadline = time.monotonic() + 10
        try:
            while time.monotonic() < deadline and not all(
                any(ch.get("topic") == topic for ch in client.channels.values()) for topic in wanted
            ):
                await client.next_message(0.2)
            matched = []
            for topic in wanted:
                channel = next((ch for ch in client.channels.values() if ch.get("topic") == topic), None)
                if channel is None:
                    raise MappingError("MAPPING_INPUT_INVALID", f"录包时缺少话题 {topic}")
                matched.append(channel)
            await client.subscribe([int(ch["id"]) for ch in matched])
            by_channel = {int(ch["id"]): ch for ch in matched}
            written = 0
            start = end = None
            counts = {topic: 0 for topic in wanted}
            with open(path, "wb") as handle:
                writer = Writer(handle)
                writer.start()
                schemas = {}
                channels = {}
                while self.stop_reason is None:
                    if shutil.disk_usage(path.parent).free < self.min_free:
                        self.stop("disk_low")
                        break
                    if written >= self.max_bytes:
                        self.stop("size_limit")
                        break
                    event = await client.next_message(0.5)
                    if event is None or event.get("kind") != "data":
                        continue
                    channel = by_channel.get(event["channelId"])
                    if channel is None:
                        continue
                    topic = channel["topic"]
                    schema_name = channel.get("schemaName") or "unknown"
                    if schema_name not in schemas:
                        schemas[schema_name] = writer.register_schema(
                            name=schema_name, encoding="ros1msg", data=(channel.get("schema") or "").encode()
                        )
                    if topic not in channels:
                        channels[topic] = writer.register_channel(
                            topic=topic, message_encoding="ros1", schema_id=schemas[schema_name]
                        )
                    stamp = int(event["timestamp"])
                    payload = bytes(event["payload"])
                    writer.add_message(channels[topic], log_time=stamp, data=payload, publish_time=stamp)
                    written += len(payload)
                    counts[topic] = counts.get(topic, 0) + 1
                    start = stamp if start is None else min(start, stamp)
                    end = stamp if end is None else max(end, stamp)
                writer.finish()
            self.meta["status"] = "ready"
            self.meta["bytes"] = written
            self.meta["startTimeNs"] = start
            self.meta["endTimeNs"] = end
            self.meta["stopReason"] = self.stop_reason or "stopped"
            for item in self.meta["topics"]:
                item["count"] = counts.get(item["topic"], 0)
            return self.meta
        finally:
            await client.close()
