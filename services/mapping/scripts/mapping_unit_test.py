#!/usr/bin/env python3
"""建图服务单元测试。不需要引擎、Bridge 或 core。

用法（services/mapping 目录下）：.venv/bin/python scripts/mapping_unit_test.py
"""
import json
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
    close = dict(decoded)
    close["ranges"] = [2.0, 2.01]
    message, dropped = convert_laser_scan(close, "front-laser", identity(), 1, "forkai", 0)
    points = message["timed_point_cloud_data"]["point_data"]
    check("相邻有效点都送给引擎", dropped == 0 and len(points) == 2, f"dropped={dropped} points={len(points)}")


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


def test_silence_follows_socket() -> None:
    stalled = OnlineStream(["/front/scan"])
    stalled.arm()
    stalled._started -= 3
    stalled._input_seen["/front/scan"] -= 3
    stalled.push("/front/scan", 1, "front_laser_link", {}, 1)
    stalled.poll_silence()
    check("送进队列不会掩盖静默", stalled.stop_reason == "input_stalled")
    fresh = OnlineStream(["/front/scan"])
    fresh.arm()
    fresh._started -= 3
    fresh._input_seen["/front/scan"] -= 3
    fresh.note_seen("/front/scan")
    fresh.poll_silence()
    check("收包回调会刷新静默", fresh.stop_reason is None)


def test_pose_queue() -> None:
    from app.engine.cartographer_client import _trajectory_options_payload

    payload = _trajectory_options_payload({"trajectory_builder_2d": {}})
    duration = payload["trajectory_builder_2d_options"]["pose_extrapolator_options"]["constant_velocity"]["pose_queue_duration"]
    check("外推队列与引擎 lua 的 0.001 秒一致", duration == 0.001, str(duration))


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


def test_demo_codecs() -> None:
    from app.assets.map_store import umcl_preview_png
    from app.assets.zip_export import export_zip
    from app.ingest.livox import imu_vector, pack_custom
    from app.ingest.ros1_codec import decode, decode_livox_custom, encode_imu, encode_livox_custom
    import importlib.util
    import tempfile
    encoded = encode_livox_custom({
        "sec": 1, "nsec": 2, "frame_id": "lidar", "timebase": 3, "lidar_id": 1,
        "points": [{"offset_time": 4, "x": 1.5, "y": -2.0, "z": 0.3, "reflectivity": 9, "tag": 2, "line": 3}],
    })
    parsed = decode_livox_custom(encoded)
    check("CustomMsg 往返", parsed["header"]["frame_id"] == "lidar" and abs(parsed["points"][0]["x"] - 1.5) < 1e-5 and parsed["points"][0]["line"] == 3)
    packed = pack_custom(parsed)
    check("Livox 打包每点 24 字节", len(packed) == 24)
    imu = encode_imu({
        "sec": 1, "nsec": 0, "frame_id": "imu",
        "angular_velocity": {"x": 0.0, "y": 0.0, "z": 0.2},
        "linear_acceleration": {"x": 0.0, "y": 0.0, "z": 1.0},
    })
    vector = imu_vector(decode("sensor_msgs/Imu", imu), 9.80665)
    check("Imu 往返并换算加速度", abs(vector[2] - 9.80665) < 1e-6 and abs(vector[5] - 0.2) < 1e-6)
    spec = importlib.util.spec_from_file_location("fake_scene3d", ROOT / "devtools" / "fake_scene3d.py")
    scene = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scene)
    hit = scene.scan_2d({"x": 5.0, "y": 8.0, "yaw": 0.0}, {"x": 0.0, "y": 0.0, "yaw": 0.0}, [0.0])
    check("水平射线打到东墙", abs(hit[0] - 25.0) < 0.05, str(hit[0]))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "project.json").write_text(json.dumps({
            "representations": [{"role": "engine-state", "kind": "engine-state", "path": "map_project"}],
        }), encoding="utf-8")
        folder = root / "map_project"
        folder.mkdir()
        (folder / "current").write_text("v001\n", encoding="utf-8")
        names = __import__("zipfile").ZipFile(__import__("io").BytesIO(export_zip(root))).namelist()
        check("目录产物写入 ZIP", "map_project/current" in names, str(names))
    preview = umcl_preview_png({
        "Header": "umcl-map", "MapRes": 0.05, "MinPose": "0 0", "MaxPose": "1000 0",
        "NumPoints": 2, "ObsPoints": [[0, 0], [1000, 0]],
    })
    _w, _h, pixels = decode_gray8(preview)
    check("UMCL 预览有占据像素", any(pixel == 100 for pixel in pixels))


def main() -> int:
    test_time()
    test_scan()
    test_urdf()
    test_umcl()
    test_rosbags()
    test_queue()
    test_silence_follows_socket()
    test_pose_queue()
    test_png()
    test_compose()
    test_plan2d()
    test_demo_codecs()
    failed = RESULTS.count(False)
    print(f"[MAP-UNIT] {len(RESULTS) - failed}/{len(RESULTS)} passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
