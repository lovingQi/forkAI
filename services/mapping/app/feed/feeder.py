"""50 毫秒或达到批次上限就发送，同一时间只有一批在途。在线发送走独立线程，避免堵住读数据。"""
import asyncio
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
        payload = list(batch)
        batch.clear()
        batch_bytes = 0
        started = 0.0
        await asyncio.to_thread(client.add_sensor_data_batch_blocking, payload)
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


async def run_recorded_feeder(messages, client, trajectory_id: int, bindings: dict, limits: dict, gate, on_progress, on_checkpoint=None) -> None:
    """离线送数：不按 50ms 定时，只按批次上限发送，并受暂停门控制。"""
    batch: list[dict] = []
    batch_bytes = 0
    max_messages = int(limits.get("maxBatchMessages") or 128)
    max_bytes = int(limits.get("maxBatchBytes") or 8 * 1024 * 1024)
    sent_scans = 0
    sent_odom = 0
    next_checkpoint = 200

    async def flush() -> None:
        nonlocal batch_bytes, sent_scans, sent_odom
        if not batch:
            return
        scans = sum(1 for item in batch if "timed_point_cloud_data" in item)
        odoms = len(batch) - scans
        await client.add_sensor_data_batch(batch)
        batch.clear()
        batch_bytes = 0
        sent_scans += scans
        sent_odom += odoms
        on_progress(sent_scans, sent_odom, None)

    for item in messages():
        await gate.wait()
        binding = bindings.get(item["topic"])
        if binding is None:
            continue
        message, _dropped = binding["convert"](item)
        size = item["size"] + 256
        if batch and (len(batch) >= max_messages or batch_bytes + size > max_bytes):
            await flush()
        batch.append(message)
        batch_bytes += size
        pending_scans = sent_scans + sum(1 for entry in batch if "timed_point_cloud_data" in entry)
        if on_checkpoint and pending_scans >= next_checkpoint:
            await flush()
            await on_checkpoint(sent_scans)
            next_checkpoint += 200
        on_progress(sent_scans, sent_odom, item.get("stamp_ns"))
    await flush()
    if on_checkpoint:
        await on_checkpoint(sent_scans)
