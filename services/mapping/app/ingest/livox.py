"""Livox CustomMsg 与 IMU 转成测试 proto 的传感器包。核对点 F 未关闭。"""
import struct


def pack_custom(decoded: dict) -> bytes:
    points = decoded.get("points") or []
    raw = bytearray(len(points) * 24)
    for index, point in enumerate(points):
        struct.pack_into(
            "<fffB3xIBB2x", raw, index * 24,
            float(point["x"]), float(point["y"]), float(point["z"]),
            int(point.get("reflectivity") or 0) & 0xFF,
            int(point.get("offset_time") or 0),
            int(point.get("line") or 0) & 0xFF,
            int(point.get("tag") or 0) & 0xFF,
        )
    return bytes(raw)


def imu_vector(decoded: dict, scale: float) -> list[float]:
    acc = decoded["linear_acceleration"]
    gyro = decoded["angular_velocity"]
    return [
        float(acc["x"]) * scale, float(acc["y"]) * scale, float(acc["z"]) * scale,
        float(gyro["x"]), float(gyro["y"]), float(gyro["z"]),
    ]


def to_sensor_message(decoded: dict, stamp_ns: int, sensor_id: str, scale: float) -> dict | None:
    if "points" in decoded:
        data = pack_custom(decoded)
        return {
            "sensor_id": sensor_id,
            "kind": "point_cloud",
            "stamp_ns": stamp_ns,
            "point_step": 24,
            "point_count": len(decoded["points"]),
            "data": data,
        }
    if "linear_acceleration" in decoded:
        return {
            "sensor_id": sensor_id,
            "kind": "imu",
            "stamp_ns": stamp_ns,
            "point_step": 0,
            "point_count": 0,
            "data": struct.pack("<6d", *imu_vector(decoded, scale)),
        }
    return None
