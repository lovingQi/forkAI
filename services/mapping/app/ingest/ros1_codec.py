"""标准 sensor_msgs/LaserScan 与 nav_msgs/Odometry 的 ROS1 编解码。

优先用 rosbags 的 ROS1 类型库反序列化；假 Bridge 用同一布局手写序列化，
单元测试会拿 rosbags 反解核对。
"""
import struct

from ..errors import MappingError


def _need(buf: bytes, offset: int, size: int) -> None:
    if offset + size > len(buf):
        raise MappingError("MAPPING_INPUT_INVALID", "ROS1 消息被截断")


def _unpack(fmt: str, buf: bytes, offset: int):
    size = struct.calcsize(fmt)
    _need(buf, offset, size)
    return struct.unpack_from(fmt, buf, offset), offset + size


def _str(buf: bytes, offset: int) -> tuple[str, int]:
    (length,), offset = _unpack("<I", buf, offset)
    _need(buf, offset, length)
    return buf[offset:offset + length].decode("utf-8", "replace"), offset + length


def _put_str(parts: list, text: str) -> None:
    raw = text.encode("utf-8")
    parts.append(struct.pack("<I", len(raw)))
    parts.append(raw)


def decode_laser_scan(payload: bytes) -> dict:
    offset = 0
    (seq,), offset = _unpack("<I", payload, offset)
    (sec, nsec), offset = _unpack("<II", payload, offset)
    frame, offset = _str(payload, offset)
    floats, offset = _unpack("<7f", payload, offset)
    (count,), offset = _unpack("<I", payload, offset)
    _need(payload, offset, count * 4)
    ranges = struct.unpack_from(f"<{count}f", payload, offset)
    return {
        "header": {"seq": seq, "stamp": {"sec": sec, "nsec": nsec}, "frame_id": frame},
        "angle_min": floats[0],
        "angle_max": floats[1],
        "angle_increment": floats[2],
        "time_increment": floats[3],
        "scan_time": floats[4],
        "range_min": floats[5],
        "range_max": floats[6],
        "ranges": list(ranges),
    }


def encode_laser_scan(scan: dict) -> bytes:
    parts = [struct.pack("<III", 0, scan["sec"], scan["nsec"])]
    _put_str(parts, scan["frame_id"])
    parts.append(struct.pack(
        "<7f",
        scan["angle_min"], scan["angle_max"], scan["angle_increment"], scan["time_increment"],
        scan.get("scan_time", 0.0), scan["range_min"], scan["range_max"],
    ))
    ranges = scan["ranges"]
    parts.append(struct.pack("<I", len(ranges)))
    parts.append(struct.pack(f"<{len(ranges)}f", *ranges))
    parts.append(struct.pack("<I", 0))
    return b"".join(parts)


def decode_odometry(payload: bytes) -> dict:
    offset = 0
    (seq,), offset = _unpack("<I", payload, offset)
    (sec, nsec), offset = _unpack("<II", payload, offset)
    frame, offset = _str(payload, offset)
    child, offset = _str(payload, offset)
    pose, offset = _unpack("<7d", payload, offset)
    offset += 36 * 8
    return {
        "header": {"seq": seq, "stamp": {"sec": sec, "nsec": nsec}, "frame_id": frame},
        "child_frame_id": child,
        "pose": {
            "pose": {
                "position": {"x": pose[0], "y": pose[1], "z": pose[2]},
                "orientation": {"x": pose[3], "y": pose[4], "z": pose[5], "w": pose[6]},
            }
        },
    }


def encode_odometry(odom: dict) -> bytes:
    parts = [struct.pack("<III", 0, odom["sec"], odom["nsec"])]
    _put_str(parts, odom["frame_id"])
    _put_str(parts, odom["child_frame_id"])
    pos = odom["position"]
    quat = odom["orientation"]
    parts.append(struct.pack("<7d", pos["x"], pos["y"], pos["z"], quat["x"], quat["y"], quat["z"], quat["w"]))
    parts.append(b"\x00" * (36 * 8))
    parts.append(b"\x00" * (6 * 8 + 36 * 8))
    return b"".join(parts)


def decode(schema_name: str, payload: bytes) -> dict:
    try:
        return decode_with_rosbags(schema_name, payload)
    except MappingError:
        raise
    except Exception:
        name = schema_name.strip()
        if name in ("sensor_msgs/LaserScan", "sensor_msgs/msg/LaserScan"):
            return decode_laser_scan(payload)
        if name in ("nav_msgs/Odometry", "nav_msgs/msg/Odometry"):
            return decode_odometry(payload)
        raise MappingError("MAPPING_INPUT_INVALID", f"不支持的消息类型 {schema_name}")


def _nsec(stamp) -> int:
    if hasattr(stamp, "nsec"):
        return int(stamp.nsec)
    return int(stamp.nanosec)


def decode_with_rosbags(schema_name: str, payload: bytes) -> dict:
    """用 rosbags 反序列化标准消息，再收成与手写解码相同的字典。"""
    from rosbags.typesys import Stores, get_typestore

    store = get_typestore(Stores.ROS1_NOETIC)
    typename = {
        "sensor_msgs/LaserScan": "sensor_msgs/msg/LaserScan",
        "sensor_msgs/msg/LaserScan": "sensor_msgs/msg/LaserScan",
        "nav_msgs/Odometry": "nav_msgs/msg/Odometry",
        "nav_msgs/msg/Odometry": "nav_msgs/msg/Odometry",
    }.get(schema_name)
    if typename is None:
        raise MappingError("MAPPING_INPUT_INVALID", f"不支持的消息类型 {schema_name}")
    msg = store.deserialize_ros1(payload, typename)
    if "LaserScan" in typename:
        return {
            "header": {
                "seq": int(msg.header.seq),
                "stamp": {"sec": int(msg.header.stamp.sec), "nsec": _nsec(msg.header.stamp)},
                "frame_id": str(msg.header.frame_id),
            },
            "angle_min": float(msg.angle_min),
            "angle_max": float(msg.angle_max),
            "angle_increment": float(msg.angle_increment),
            "time_increment": float(msg.time_increment),
            "scan_time": float(msg.scan_time),
            "range_min": float(msg.range_min),
            "range_max": float(msg.range_max),
            "ranges": [float(v) for v in msg.ranges],
        }
    return {
        "header": {
            "seq": int(msg.header.seq),
            "stamp": {"sec": int(msg.header.stamp.sec), "nsec": _nsec(msg.header.stamp)},
            "frame_id": str(msg.header.frame_id),
        },
        "child_frame_id": str(msg.child_frame_id),
        "pose": {
            "pose": {
                "position": {"x": float(msg.pose.pose.position.x), "y": float(msg.pose.pose.position.y), "z": float(msg.pose.pose.position.z)},
                "orientation": {
                    "x": float(msg.pose.pose.orientation.x),
                    "y": float(msg.pose.pose.orientation.y),
                    "z": float(msg.pose.pose.orientation.z),
                    "w": float(msg.pose.pose.orientation.w),
                },
            }
        },
    }
