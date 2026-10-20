"""50 毫秒或达到批次上限就发送，同一时间只有一批在途。"""
import time

from ..ingest.online_stream import OnlineStream

CLIENT_ID = "forkai"


async def run_feeder(stream: OnlineStream, client, trajectory_id: int, bindings: dict, limits: dict, on_progress) -> None:
    batch: list[dict] = []
    batch_bytes = 0
    started = 0.0
    max_messages = int(limits.get("maxBatchMessages") or 128)
    max_bytes = int(limits.get("maxBatchBytes") or 8 * 1024 * 1024)
    sent_scans = 0
    sent_odom = 0

    async def flush() -> None:
        nonlocal batch_bytes, started, sent_scans, sent_odom
        if not batch:
            return
        scans = sum(1 for item in batch if "timed_point_cloud_data" in item)
        odoms = len(batch) - scans
        await client.add_sensor_data_batch(batch)
        batch.clear()
        batch_bytes = 0
        started = 0.0
        sent_scans += scans
        sent_odom += odoms
        on_progress(sent_scans, sent_odom)

    while True:
        stream.poll_silence()
        item = stream.pop()
        if item is None:
            if batch and started and time.monotonic() - started >= 0.05:
                await flush()
            if stream.stopped and not stream.queue:
                break
            await _sleep()
            continue
        binding = bindings.get(item["topic"])
        if binding is None:
            continue
        message, dropped = binding["convert"](item)
        stream.stats["droppedRanges"] += dropped
        size = item["size"] + 256
        if batch and (len(batch) >= max_messages or batch_bytes + size > max_bytes):
            await flush()
        if not batch:
            started = time.monotonic()
        batch.append(message)
        batch_bytes += size
        if time.monotonic() - started >= 0.05 or len(batch) >= max_messages:
            await flush()
    await flush()


async def _sleep() -> None:
    import asyncio
    await asyncio.sleep(0.01)
