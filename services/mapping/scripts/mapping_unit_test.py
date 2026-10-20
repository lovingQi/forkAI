#!/usr/bin/env python3
"""建图服务单元测试。不需要引擎、Bridge 或 core。

用法（services/mapping 目录下）：.venv/bin/python scripts/mapping_unit_test.py
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.assets.umcl import is_umcl_json_map
from app.feed.convert import convert_laser_scan
from app.geom import from_universal_ticks, identity, to_universal_ticks
from app.ingest.online_stream import OnlineStream
from app.ingest.ros1_codec import decode_with_rosbags, encode_laser_scan, encode_odometry
from app.preview.compositor import compose_occupancy
from app.preview.png import decode_gray8, encode_gray8
from app.robot.urdf import load_urdf, transform

RESULTS = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append(cond)
    print(f"[MAP-UNIT] {'PASS' if cond else 'FAIL'}  {name}  {detail}", flush=True)


def test_time() -> None:
    stamp = 1_700_000_000_123_456_789
    ticks = to_universal_ticks(stamp)
    back = from_universal_ticks(ticks)
    check("时间换算往返误差小于 100 纳秒", abs(back - stamp) < 100, f"{back - stamp}")


def test_scan() -> None:
    decoded = {
        "header": {"stamp": {"sec": 10, "nsec": 0}, "frame_id": "front_laser_link"},
        "angle_min": 0.0,
        "angle_increment": 0.0,
        "time_increment": 0.0,
        "range_min": 0.1,
        "range_max": 10.0,
        "ranges": [float("nan"), float("inf"), 0.01, 2.0],
    }
    message, dropped = convert_laser_scan(decoded, "front-laser", identity(), 1, "forkai", 0)
    points = message["timed_point_cloud_data"]["point_data"]
    check("无效点被丢弃", dropped == 3 and len(points) == 1, f"dropped={dropped} points={len(points)}")
    check("有效点落在 x 轴", abs(points[0]["position"]["x"] - 2.0) < 1e-6)


def test_urdf() -> None:
    model = load_urdf(ROOT / "config" / "robot.urdf")
    front = transform(model, "base_link", "front_laser_link")
    check("前激光外参 x 约 1.2 米", abs(front["translation"]["x"] - 1.2) < 1e-6)
    model["joints"][0]["type"] = "revolute"
    try:
        transform(model, "base_link", "front_laser_link")
        check("非固定关节被拒绝", False)
    except Exception as exc:
        check("非固定关节被拒绝", "MAPPING_CALIBRATION_MISSING" in str(getattr(exc, "code", "")))


def test_umcl() -> None:
    good = {"Header": "umcl-map", "MapRes": 0.05, "MinPose": "0 0", "MaxPose": "1 1", "NumPoints": 1, "ObsPoints": [[0, 0]]}
    check("合法 UMCL", is_umcl_json_map(good))
    bad = dict(good)
    bad["NumPoints"] = 2
    check("点数不符的 UMCL 被拒绝", not is_umcl_json_map(bad))
    check("空对象不是 UMCL", not is_umcl_json_map({}))


def test_rosbags() -> None:
    scan = encode_laser_scan({
        "sec": 3, "nsec": 250, "frame_id": "front_laser_link",
        "angle_min": -1.0, "angle_max": 1.0, "angle_increment": 0.5, "time_increment": 0.0,
        "range_min": 0.05, "range_max": 30.0, "ranges": [1.5, 2.5],
    })
    decoded = decode_with_rosbags("sensor_msgs/LaserScan", scan)
    check("rosbags 能解激光", decoded["header"]["frame_id"] == "front_laser_link" and abs(decoded["ranges"][1] - 2.5) < 1e-5)
    odom = encode_odometry({
        "sec": 4, "nsec": 0, "frame_id": "odom", "child_frame_id": "base_link",
        "position": {"x": 1.25, "y": 0.0, "z": 0.0},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
    })
    parsed = decode_with_rosbags("nav_msgs/Odometry", odom)
    check("rosbags 能解里程计", parsed["child_frame_id"] == "base_link" and abs(parsed["pose"]["pose"]["position"]["x"] - 1.25) < 1e-6)


def test_queue() -> None:
    stream = OnlineStream(["/front/scan"])
    stream.arm()
    for index in range(1000):
        stream.push("/front/scan", index + 1, "front_laser_link", {}, 1)
    before = stream.stats["rejected"]
    stream.push("/front/scan", 1001, "front_laser_link", {}, 1)
    check("超过 1000 条被拒绝", stream.stats["rejected"] == before + 1)
    stream.queue[0]["queued_at"] -= 3
    stream._drop_expired()
    check("超过 2 秒的数据被丢弃", stream.stats["rejected"] >= before + 2)
    try:
        stream.push("/front/scan", 1, "front_laser_link", {}, 1)
        check("时间回退会停止", False)
    except Exception:
        check("时间回退会停止", stream.stop_reason == "input_invalid")


def test_png() -> None:
    raw = bytes([0, 10, 255, 40])
    blob = encode_gray8(2, 2, raw)
    width, height, pixels = decode_gray8(blob)
    check("PNG 往返", (width, height, pixels) == (2, 2, raw))


def test_compose() -> None:
    def cells(width: int, height: int, occupied: tuple[int, int]) -> bytes:
        raw = bytearray(width * height * 2)
        index = (occupied[1] * width + occupied[0]) * 2
        raw[index] = 0
        raw[index + 1] = 255
        return bytes(raw)

    pose = identity()
    other = identity()
    other["translation"] = {"x": 1.0, "y": 0.0, "z": 0.0}
    grid = compose_occupancy([
        {"pose": pose, "texture": {"width": 4, "height": 4, "resolution": 0.05, "slicePose": identity(), "cells": cells(4, 4, (1, 1))}},
        {"pose": other, "texture": {"width": 4, "height": 4, "resolution": 0.05, "slicePose": identity(), "cells": cells(4, 4, (2, 2))}},
    ])
    occupied = sum(1 for cell in grid["cells"] if cell >= 0)
    check("两张子图能拼出占据栅格", grid["width"] > 0 and grid["height"] > 0 and occupied > 0, f"occupied={occupied}")


def test_plan2d() -> None:
    from app.incremental.plan2d import json_pose_to_state, plan_sha256, transform_incremental_map
    state = json_pose_to_state({"xMm": 1000, "yMm": 0, "yawRad": 0}, {"txMm": 0, "tyMm": 0, "yawRad": 0})
    check("毫米位姿换成米", abs(state["x"] - 1) < 1e-9 and state["y"] == 0)
    umcl = {"Header": "umcl-map", "MapRes": 0.05, "MinPose": "0 0", "MaxPose": "1 1", "NumPoints": 1, "ObsPoints": [[0, 0]]}
    moved = transform_incremental_map(umcl, {"txMm": 10, "tyMm": 0, "yawRad": 0})
    check("增量地图按毫米平移", moved["ObsPoints"][0][0] == 10)
    check("计划哈希稳定", len(plan_sha256([], state, None)) == 64)


def main() -> int:
    test_time()
    test_scan()
    test_urdf()
    test_umcl()
    test_rosbags()
    test_queue()
    test_png()
    test_compose()
    test_plan2d()
    failed = RESULTS.count(False)
    print(f"[MAP-UNIT] {len(RESULTS) - failed}/{len(RESULTS)} passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
