"""有界队列：1000 条或 32 MiB，排队超过 2 秒丢弃。每个话题时间必须非递减。"""
import time

from ..errors import MappingError

QUEUE_MESSAGES = 1000
QUEUE_BYTES = 32 * 1024 * 1024
QUEUE_AGE_S = 2.0
SILENCE_S = 2.0


class OnlineStream:
    def __init__(self, topics: list[str]):
        self.topics = list(topics)
        self.queue: list[dict] = []
        self.bytes = 0
        self.stop_reason: str | None = None
        self.stats = {"received": 0, "accepted": 0, "rejected": 0, "pending": 0, "droppedRanges": 0}
        self._last_stamp: dict[str, int] = {}
        self._last_frame: dict[str, str] = {}
        self._last_seen = {topic: time.monotonic() for topic in topics}
        self._started = time.monotonic()
        self.armed = False

    @property
    def stopped(self) -> bool:
        return self.stop_reason is not None

    def stop(self, reason: str) -> None:
        if self.stop_reason is None:
            self.stop_reason = reason

    def arm(self) -> None:
        now = time.monotonic()
        self._started = now
        self._last_seen = {topic: now for topic in self.topics}
        self.armed = True

    def push(self, topic: str, stamp_ns: int, frame: str, decoded: dict, size: int) -> None:
        self.stats["received"] += 1
        if self.stop_reason:
            self.stats["rejected"] += 1
            return
        if topic not in self._last_seen:
            self.stats["rejected"] += 1
            return
        previous = self._last_stamp.get(topic)
        if previous is not None and stamp_ns < previous:
            self.stop("input_invalid")
            raise MappingError("MAPPING_INPUT_INVALID", f"{topic} 时间回退")
        prev_frame = self._last_frame.get(topic)
        if prev_frame is not None and prev_frame != frame:
            self.stop("input_invalid")
            raise MappingError("MAPPING_INPUT_INVALID", f"{topic} 坐标系发生变化")
        self._last_stamp[topic] = stamp_ns
        self._last_frame[topic] = frame
        self._last_seen[topic] = time.monotonic()
        self._drop_expired()
        if len(self.queue) >= QUEUE_MESSAGES or self.bytes + size > QUEUE_BYTES:
            self.stats["rejected"] += 1
            return
        self.queue.append({
            "topic": topic,
            "stamp_ns": stamp_ns,
            "frame": frame,
            "decoded": decoded,
            "size": size,
            "queued_at": time.monotonic(),
        })
        self.bytes += size
        self.stats["accepted"] += 1
        self.stats["pending"] = len(self.queue)

    def poll_silence(self) -> None:
        if not self.armed or self.stop_reason:
            return
        if time.monotonic() - self._started < SILENCE_S:
            return
        now = time.monotonic()
        for topic, seen in self._last_seen.items():
            if now - seen >= SILENCE_S:
                self.stop("input_stalled")
                return

    def pop(self) -> dict | None:
        self._drop_expired()
        if not self.queue:
            self.stats["pending"] = 0
            return None
        item = self.queue.pop(0)
        self.bytes -= item["size"]
        self.stats["pending"] = len(self.queue)
        return item

    def _drop_expired(self) -> None:
        now = time.monotonic()
        kept = []
        for item in self.queue:
            if now - item["queued_at"] > QUEUE_AGE_S:
                self.bytes -= item["size"]
                self.stats["rejected"] += 1
            else:
                kept.append(item)
        self.queue = kept
        self.stats["pending"] = len(self.queue)
