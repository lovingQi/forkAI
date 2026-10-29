"""UMCL 语义对象。坐标用毫米，角度用度。"""
import math

from ..errors import MappingError

MAX_POINTS = 2_000_000
ZONE_GROUPS = (
    "ReflectorSector",
    "LandmarkLocalizationSector",
    "LineSector",
    "QrcodeSector",
    "RelocationSector",
    "LaserIgnoreZone",
    "StableSector",
    "LocalizationMapExcludeZone",
)
POINT_GROUPS = ("Reflector", "Pole", "QrCode", "Dock", "GoalWithHeading", "PathPoint")
CONNECTION_KEYS = ("name", "cost", "posture", "type", "x1", "y1", "x2", "y2")


def resolution_mm(map_res: float) -> float:
    """建图写出的 MapRes 0.05 表示米，点坐标是毫米。"""
    if map_res < 1:
        return map_res * 1000
    return map_res


def has_functional_zones(umcl: dict) -> bool:
    objs = umcl.get("Objs")
    if not isinstance(objs, dict):
        return False
    return any(_zone_entries(objs.get(group)) for group in ZONE_GROUPS)


def validate_revision(umcl: dict, parent_yaw: float, next_yaw: float) -> None:
    if not isinstance(umcl, dict):
        raise MappingError("MAPPING_INPUT_INVALID", "地图内容无效")
    from .umcl import is_umcl_json_map

    if not is_umcl_json_map(umcl):
        raise MappingError("MAPPING_INPUT_INVALID", "map.json 不是合法 UMCL 地图")
    count = sum(len(column) - 1 for column in umcl["ObsPoints"])
    if count > MAX_POINTS:
        raise MappingError("MAPPING_INPUT_INVALID", "障碍点超过 200 万")
    _validate_objects(umcl.get("Objs"))
    if has_functional_zones(umcl) and not _quarter_turn(next_yaw - parent_yaw):
        raise MappingError("MAPPING_INPUT_INVALID", "有功能区时整图旋转只能是 90° 的整数倍")


def normalize_umcl(umcl: dict) -> dict:
    """写成整数毫米，并重算点数和范围。"""
    columns: dict[int, set[int]] = {}
    for column in umcl.get("ObsPoints") or []:
        x = _mm(column[0])
        for raw_y in column[1:]:
            columns.setdefault(x, set()).add(_mm(raw_y))
    obs = [[x, *sorted(ys)] for x, ys in sorted(columns.items()) if ys]
    if not obs:
        obs = [[0, 0]]
    count = sum(len(column) - 1 for column in obs)
    xs = [column[0] for column in obs]
    ys = [value for column in obs for value in column[1:]]
    out = dict(umcl)
    out["ObsPoints"] = obs
    out["NumPoints"] = count
    out["MinPose"] = f"{min(xs)} {min(ys)}"
    out["MaxPose"] = f"{max(xs)} {max(ys)}"
    if isinstance(out.get("Objs"), dict):
        out["Objs"] = _normalize_objs(out["Objs"])
    return out


def _normalize_objs(objs: dict) -> dict:
    result = {}
    for group, entries in objs.items():
        if not isinstance(entries, list):
            result[group] = entries
            continue
        result[group] = [_normalize_entry(group, entry) for entry in entries]
    return result


def _normalize_entry(group: str, entry):
    if not isinstance(entry, dict):
        return entry
    item = dict(entry)
    if group == "FeatureLine":
        points = _point_list(item.get("points"))
        if len(points) == 2:
            start, end = points
            item["points"] = _format_points([start[0], start[1], end[0], end[1]])
            item["pose"] = _format_pose(
                (start[0] + end[0]) / 2,
                (start[1] + end[1]) / 2,
                math.degrees(math.atan2(end[1] - start[1], end[0] - start[0])),
            )
        return item
    if group in ZONE_GROUPS:
        points = _point_list(item.get("points"))
        if len(points) == 2:
            min_x = min(points[0][0], points[1][0])
            min_y = min(points[0][1], points[1][1])
            max_x = max(points[0][0], points[1][0])
            max_y = max(points[0][1], points[1][1])
            item["points"] = _format_points([min_x, min_y, max_x, max_y])
            yaw = _pose_yaw(item.get("pose"))
            item["pose"] = _format_pose((min_x + max_x) / 2, (min_y + max_y) / 2, yaw)
        return item
    if group in POINT_GROUPS:
        pose = _pose_xy(item.get("pose"))
        if pose is not None:
            item["pose"] = _format_pose(pose[0], pose[1], _pose_yaw(item.get("pose")))
            if group in ("Reflector", "Pole"):
                item["points"] = _format_points([pose[0], pose[1], pose[0], pose[1]])
        if group == "PathPoint" and isinstance(item.get("connections"), list):
            item["connections"] = [_normalize_connection(conn) for conn in item["connections"] if isinstance(conn, dict)]
    return item


def _validate_objects(objs) -> None:
    if objs is None:
        return
    if not isinstance(objs, dict):
        raise MappingError("MAPPING_INPUT_INVALID", "Objs 必须按类型分组")
    names = set()
    points = objs.get("PathPoint")
    if isinstance(points, list):
        for entry in points:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str) and entry["name"]:
                names.add(entry["name"])
    for group, entries in objs.items():
        if not isinstance(entries, list):
            raise MappingError("MAPPING_INPUT_INVALID", f"{group} 必须是数组")
        for entry in entries:
            if not isinstance(entry, dict):
                raise MappingError("MAPPING_INPUT_INVALID", f"{group} 里有无效对象")
            if group in ZONE_GROUPS:
                bounds = _point_list(entry.get("points"))
                if len(bounds) != 2:
                    raise MappingError("MAPPING_INPUT_INVALID", f"{group} 需要两个角点")
                if bounds[0][0] == bounds[1][0] or bounds[0][1] == bounds[1][1]:
                    raise MappingError("MAPPING_INPUT_INVALID", "功能区的宽和高必须大于 0")
            if group == "PathPoint":
                for conn in entry.get("connections") or []:
                    if not isinstance(conn, dict) or not isinstance(conn.get("name"), str) or not conn["name"]:
                        raise MappingError("MAPPING_INPUT_INVALID", "路径连接缺少目标名称")
                    if conn["name"] not in names:
                        raise MappingError("MAPPING_INPUT_INVALID", "路径连接指向不存在的路径点")
                    for key in ("x1", "y1", "x2", "y2", "cost", "type"):
                        if key in conn and not _finite(conn[key]):
                            raise MappingError("MAPPING_INPUT_INVALID", f"路径连接的 {key} 无效")


def _zone_entries(entries) -> bool:
    if not isinstance(entries, list):
        return False
    return any(isinstance(entry, dict) and len(_point_list(entry.get("points"))) == 2 for entry in entries)


def _quarter_turn(delta: float) -> bool:
    if not _finite(delta):
        return False
    quarters = delta / (math.pi / 2)
    return abs(quarters - round(quarters)) <= 1e-6


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _mm(value) -> int:
    return int(round(float(value)))


def _point_list(value) -> list[tuple[int, int]]:
    if not isinstance(value, str):
        return []
    numbers = []
    for part in value.split():
        try:
            numbers.append(_mm(part))
        except ValueError:
            return []
    if len(numbers) % 2:
        return []
    return [(numbers[index], numbers[index + 1]) for index in range(0, len(numbers), 2)]


def _pose_xy(value):
    parts = str(value or "").split()
    if len(parts) < 2:
        return None
    try:
        return _mm(parts[0]), _mm(parts[1])
    except ValueError:
        return None


def _pose_yaw(value) -> float:
    parts = str(value or "").split()
    if len(parts) < 3:
        return 0.0
    try:
        yaw = float(parts[2])
    except ValueError:
        return 0.0
    return round(yaw, 2)


def _format_points(values: list[float]) -> str:
    return " ".join(str(_mm(value)) for value in values)


def _format_pose(x: float, y: float, yaw: float) -> str:
    yaw_text = f"{round(yaw, 2):.2f}".rstrip("0").rstrip(".")
    return f"{_mm(x)} {_mm(y)} {yaw_text or '0'}"


def _normalize_connection(conn: dict) -> dict:
    item = {"name": conn.get("name")}
    for key in ("cost", "posture", "type", "x1", "y1", "x2", "y2"):
        if key in conn:
            item[key] = conn[key]
    return item
