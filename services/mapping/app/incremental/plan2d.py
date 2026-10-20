"""UMCL 毫米坐标（J）和引擎米坐标（G）之间的变换。"""
import hashlib
import json
import math


def _rotate(yaw: float, x: float, y: float) -> tuple[float, float]:
    c, s = math.cos(yaw), math.sin(yaw)
    return c * x - s * y, s * x + c * y


def json_pose_to_state(pose: dict, transform: dict) -> dict:
    dx = float(pose["xMm"]) - float(transform.get("txMm") or 0)
    dy = float(pose["yMm"]) - float(transform.get("tyMm") or 0)
    x, y = _rotate(-float(transform.get("yawRad") or 0), dx, dy)
    return {"x": x / 1000.0, "y": y / 1000.0, "yaw": float(pose["yawRad"]) - float(transform.get("yawRad") or 0)}


def json_region_to_state(region: dict, transform: dict) -> list[dict]:
    corners = [
        {"xMm": region["minX"], "yMm": region["minY"], "yawRad": 0},
        {"xMm": region["maxX"], "yMm": region["minY"], "yawRad": 0},
        {"xMm": region["maxX"], "yMm": region["maxY"], "yawRad": 0},
        {"xMm": region["minX"], "yMm": region["maxY"], "yawRad": 0},
    ]
    return [{"x": item["x"], "y": item["y"], "z": 0.0} for item in (json_pose_to_state(c, transform) for c in corners)]


def initial_relative_pose(target: dict, reference: dict) -> dict:
    dx = target["x"] - reference["x"]
    dy = target["y"] - reference["y"]
    x, y = _rotate(-reference.get("yaw", 0.0), dx, dy)
    yaw = target["yaw"] - reference.get("yaw", 0.0)
    half = yaw / 2
    return {
        "translation": {"x": x, "y": y, "z": 0.0},
        "rotation": {"x": 0.0, "y": 0.0, "z": math.sin(half), "w": math.cos(half)},
    }


def transform_incremental_map(umcl: dict, transform: dict) -> dict:
    yaw = float(transform.get("yawRad") or 0)
    tx = float(transform.get("txMm") or 0)
    ty = float(transform.get("tyMm") or 0)
    if yaw == 0 and tx == 0 and ty == 0:
        return umcl
    if umcl.get("Lines") or umcl.get("Objs"):
        raise ValueError("增量导出的地图不能带标注")
    moved = []
    xs, ys = [], []
    count = 0
    for column in umcl.get("ObsPoints") or []:
        x, y = _rotate(yaw, float(column[0]), 0)
        # 列首是 x，其余是同一列上的 y。只平移旋转列坐标。
        nx = x + tx
        new_col = [nx]
        for raw_y in column[1:]:
            _, ny = _rotate(yaw, 0, float(raw_y))
            new_col.append(ny + ty)
            ys.append(ny + ty)
            count += 1
        xs.append(nx)
        moved.append(new_col)
    out = dict(umcl)
    out["ObsPoints"] = moved
    out["NumPoints"] = count
    if xs and ys:
        out["MinPose"] = f"{min(xs)} {min(ys)}"
        out["MaxPose"] = f"{max(xs)} {max(ys)}"
    return out


def plan_sha256(selected: list[dict], pose: dict, region: dict | None) -> str:
    payload = json.dumps({"selected": selected, "pose": pose, "region": region}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def json_pose3d_to_state(pose: dict, transform: dict) -> dict:
    flat = json_pose_to_state(pose, transform)
    roll = float(pose.get("rollRad") or 0)
    pitch = float(pose.get("pitchRad") or 0)
    yaw = flat["yaw"]
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return {
        "translation": {"x": flat["x"], "y": flat["y"], "z": float(pose.get("zM") or 0)},
        "rotation": {
            "x": sr * cp * cy - cr * sp * sy,
            "y": cr * sp * cy + sr * cp * sy,
            "z": cr * cp * sy - sr * sp * cy,
            "w": cr * cp * cy + sr * sp * sy,
        },
    }
