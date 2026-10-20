"""LaserScan / Odometry → Cartographer 传感器字典。Inf 无回波按越界丢弃并计数。"""
import math

from ..geom import compose, identity, invert, to_universal_ticks, transform_point


def _stamp_ns(decoded: dict, fallback_ns: int) -> int:
    stamp = decoded["header"]["stamp"]
    sec = int(stamp["sec"])
    nsec = int(stamp.get("nsec", stamp.get("nanosec", 0)))
    if sec == 0 and nsec == 0:
        return fallback_ns
    return sec * 1_000_000_000 + nsec


def convert_laser_scan(decoded: dict, sensor_id: str, extrinsic: dict, trajectory_id: int, client_id: str, fallback_ns: int) -> tuple[dict, int]:
    ranges = decoded["ranges"]
    if not ranges:
        raise ValueError("激光没有测距点")
    angle_min = float(decoded["angle_min"])
    angle_inc = float(decoded["angle_increment"])
    time_inc = float(decoded["time_increment"])
    range_min = float(decoded["range_min"])
    range_max = float(decoded["range_max"])
    points = []
    dropped = 0
    last_relative = 0.0
    for index, raw in enumerate(ranges):
        value = float(raw)
        if not math.isfinite(value) or value < range_min or value > range_max:
            dropped += 1
            continue
        angle = angle_min + index * angle_inc
        last_relative = index * time_inc
        points.append({
            "position": transform_point(extrinsic, {"x": value * math.cos(angle), "y": value * math.sin(angle), "z": 0.0}),
            "time": last_relative,
        })
    if not points:
        raise ValueError("激光没有有效测距")
    for point in points:
        point["time"] -= last_relative
    stamp = _stamp_ns(decoded, fallback_ns)
    end_ns = stamp + int(round(last_relative * 1_000_000_000))
    return {
        "sensor_metadata": {"trajectory_id": trajectory_id, "sensor_id": sensor_id, "client_id": client_id},
        "timed_point_cloud_data": {
            "timestamp": to_universal_ticks(end_ns),
            "origin": dict(extrinsic["translation"]),
            "point_data": points,
        },
    }, dropped


def convert_odometry(decoded: dict, sensor_id: str, extrinsic: dict, trajectory_id: int, client_id: str, fallback_ns: int) -> dict:
    pose = decoded["pose"]["pose"]
    body = {"translation": dict(pose["position"]), "rotation": dict(pose["orientation"])}
    # 里程计给的是跟踪坐标系（child）在父坐标系下的位姿。外参是 tracking←sensor 时取逆再复合。
    tracking = compose(body, invert(extrinsic if extrinsic else identity()))
    return {
        "sensor_metadata": {"trajectory_id": trajectory_id, "sensor_id": sensor_id, "client_id": client_id},
        "odometry_data": {
            "timestamp": to_universal_ticks(_stamp_ns(decoded, fallback_ns)),
            "pose": tracking,
        },
    }
