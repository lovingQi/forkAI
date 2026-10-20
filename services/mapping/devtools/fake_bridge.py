#!/usr/bin/env python3
"""假 Foxglove Bridge。默认双激光约 30Hz、里程计 100Hz。控制口：同一端口的 /control。"""
import argparse
import asyncio
import json
import struct
import sys
import time
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ingest.ros1_codec import encode_laser_scan, encode_odometry

CHANNELS = [
    {"id": 1, "topic": "/front/scan", "encoding": "ros1", "schemaName": "sensor_msgs/LaserScan", "schema": ""},
    {"id": 2, "topic": "/back/scan", "encoding": "ros1", "schemaName": "sensor_msgs/LaserScan", "schema": ""},
    {"id": 3, "topic": "/jarvis/odom", "encoding": "ros1", "schemaName": "nav_msgs/Odometry", "schema": ""},
]
FRAMES = {1: "front_laser_link", 2: "back_laser_link"}


class Bridge:
    def __init__(self):
        self.drop: set[str] = set()
        self.rewind = False
        self.sockets: set = set()

    async def handler(self, ws) -> None:
        path = ws.request.path if hasattr(ws, "request") else getattr(ws, "path", "/")
        if path.startswith("/control"):
            async for raw in ws:
                cmd = json.loads(raw)
                if cmd.get("cmd") == "drop":
                    self.drop.add(cmd["topic"])
                elif cmd.get("cmd") == "rewind":
                    self.rewind = True
                elif cmd.get("cmd") == "close":
                    for sock in list(self.sockets):
                        await sock.close()
            return
        self.sockets.add(ws)
        try:
            await ws.send(json.dumps({"op": "serverInfo", "name": "fake-bridge", "capabilities": [], "supportedEncodings": ["ros1"]}))
            await ws.send(json.dumps({"op": "advertise", "channels": CHANNELS}))
            subs: dict[int, int] = {}

            async def read() -> None:
                async for raw in ws:
                    if isinstance(raw, bytes):
                        continue
                    body = json.loads(raw)
                    if body.get("op") == "subscribe":
                        for item in body.get("subscriptions") or []:
                            subs[int(item["id"])] = int(item["channelId"])

            reader = asyncio.create_task(read())
            tick = 0
            try:
                while True:
                    await asyncio.sleep(0.01)
                    tick += 1
                    stamp = time.time() - tick if self.rewind else time.time()
                    sec = int(stamp)
                    nsec = int((stamp - sec) * 1_000_000_000) % 1_000_000_000
                    ts_ns = sec * 1_000_000_000 + nsec
                    if tick % 3 == 0:
                        for channel_id, frame in FRAMES.items():
                            topic = CHANNELS[channel_id - 1]["topic"]
                            if topic in self.drop:
                                continue
                            payload = encode_laser_scan({
                                "sec": sec, "nsec": nsec, "frame_id": frame,
                                "angle_min": -1.57, "angle_max": 1.57, "angle_increment": 0.01,
                                "time_increment": 0.0, "range_min": 0.05, "range_max": 30.0,
                                "ranges": [1.5] * 1081,
                            })
                            await self._send(ws, subs, channel_id, ts_ns, payload)
                    if "/jarvis/odom" not in self.drop:
                        payload = encode_odometry({
                            "sec": sec, "nsec": nsec, "frame_id": "odom", "child_frame_id": "base_link",
                            "position": {"x": tick * 0.001, "y": 0.0, "z": 0.0},
                            "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                        })
                        await self._send(ws, subs, 3, ts_ns, payload)
            finally:
                reader.cancel()
        finally:
            self.sockets.discard(ws)

    async def _send(self, ws, subs, channel_id, ts_ns, payload) -> None:
        for sub_id, bound in list(subs.items()):
            if bound == channel_id:
                await ws.send(struct.pack("<BIQ", 1, sub_id, ts_ns) + payload)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    bridge = Bridge()
    async with websockets.serve(bridge.handler, "127.0.0.1", args.port, subprotocols=["foxglove.websocket.v1"]):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
