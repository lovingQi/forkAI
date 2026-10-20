"""刚体变换。旋转公式与 robospace laserScanFeeder / 纹理拼图一致。"""
import math

OFFSET_TICKS = 621355968000000000


def to_universal_ticks(unix_ns: int) -> int:
    seconds = unix_ns // 1_000_000_000
    remainder = unix_ns % 1_000_000_000
    return (seconds + 719_162 * 24 * 60 * 60) * 10_000_000 + (remainder + 50) // 100


def from_universal_ticks(ticks: int) -> int:
    return (int(ticks) - OFFSET_TICKS) * 100


def _js_round(value: float) -> int:
    return int(math.floor(value + 0.5)) if value >= 0 else int(math.ceil(value - 0.5))


def rpy_to_quat(roll: float, pitch: float, yaw: float) -> dict:
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return {
        "x": sr * cp * cy - cr * sp * sy,
        "y": cr * sp * cy + sr * cp * sy,
        "z": cr * cp * sy - sr * sp * cy,
        "w": cr * cp * cy + sr * sp * sy,
    }


def quat_norm(q: dict) -> dict:
    n = math.hypot(q["x"], q["y"], q["z"], q["w"])
    if not math.isfinite(n) or n <= 0:
        raise ValueError("四元数无效")
    return {k: q[k] / n for k in ("x", "y", "z", "w")}


def quat_inv(q: dict) -> dict:
    n2 = q["x"] ** 2 + q["y"] ** 2 + q["z"] ** 2 + q["w"] ** 2
    if not math.isfinite(n2) or n2 <= 0:
        raise ValueError("四元数无效")
    return {"x": -q["x"] / n2, "y": -q["y"] / n2, "z": -q["z"] / n2, "w": q["w"] / n2}


def quat_mul(a: dict, b: dict) -> dict:
    return quat_norm(
        {
            "x": a["w"] * b["x"] + a["x"] * b["w"] + a["y"] * b["z"] - a["z"] * b["y"],
            "y": a["w"] * b["y"] - a["x"] * b["z"] + a["y"] * b["w"] + a["z"] * b["x"],
            "z": a["w"] * b["z"] + a["x"] * b["y"] - a["y"] * b["x"] + a["z"] * b["w"],
            "w": a["w"] * b["w"] - a["x"] * b["x"] - a["y"] * b["y"] - a["z"] * b["z"],
        }
    )


def _cross(a: dict, b: dict) -> dict:
    return {
        "x": a["y"] * b["z"] - a["z"] * b["y"],
        "y": a["z"] * b["x"] - a["x"] * b["z"],
        "z": a["x"] * b["y"] - a["y"] * b["x"],
    }


def rotate(q: dict, v: dict) -> dict:
    qn = quat_norm(q)
    uv = _cross(qn, v)
    uuv = _cross(qn, uv)
    return {
        "x": v["x"] + 2 * (qn["w"] * uv["x"] + uuv["x"]),
        "y": v["y"] + 2 * (qn["w"] * uv["y"] + uuv["y"]),
        "z": v["z"] + 2 * (qn["w"] * uv["z"] + uuv["z"]),
    }


def compose(a: dict, b: dict) -> dict:
    t = rotate(a["rotation"], b["translation"])
    return {
        "translation": {
            "x": a["translation"]["x"] + t["x"],
            "y": a["translation"]["y"] + t["y"],
            "z": a["translation"]["z"] + t["z"],
        },
        "rotation": quat_mul(a["rotation"], b["rotation"]),
    }


def invert(tf: dict) -> dict:
    rotation = quat_inv(tf["rotation"])
    translation = rotate(
        rotation,
        {"x": -tf["translation"]["x"], "y": -tf["translation"]["y"], "z": -tf["translation"]["z"]},
    )
    return {"translation": translation, "rotation": rotation}


def transform_point(tf: dict, point: dict) -> dict:
    rotated = rotate(tf["rotation"], point)
    return {
        "x": tf["translation"]["x"] + rotated["x"],
        "y": tf["translation"]["y"] + rotated["y"],
        "z": tf["translation"]["z"] + rotated["z"],
    }


def yaw_of(q: dict) -> float:
    return math.atan2(2 * (q["w"] * q["z"] + q["x"] * q["y"]), 1 - 2 * (q["y"] ** 2 + q["z"] ** 2))


def identity() -> dict:
    return {"translation": {"x": 0.0, "y": 0.0, "z": 0.0}, "rotation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}}


def js_round(value: float) -> int:
    return _js_round(value)
