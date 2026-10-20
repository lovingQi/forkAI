"""演示用仓库。雷达点在世界坐标系里，假引擎只做累加，不做 SLAM。"""
import math

WIDTH = 30.0
DEPTH = 16.0
HEIGHT = 3.0
SPEED = 1.2
_LOOP = ((4.0, 4.0), (26.0, 4.0), (26.0, 12.0), (4.0, 12.0))
_BOXES = (
    (10.0, 4.0, 11.0, 5.0),
    (20.0, 11.0, 21.0, 12.0),
    (12.0, 1.0, 20.0, 2.0),
    (12.0, 14.0, 20.0, 15.0),
)
_WALLS = (
    (0.0, 0.0, WIDTH, 0.0),
    (WIDTH, 0.0, WIDTH, DEPTH),
    (WIDTH, DEPTH, 0.0, DEPTH),
    (0.0, DEPTH, 0.0, 0.0),
)


def _surface() -> list[tuple[float, float, float]]:
    points = []
    step = 0.28

    def add(x: float, y: float, z: float) -> None:
        points.append((x, y, z))

    y = 0.0
    while y <= DEPTH:
        z = 0.0
        while z <= HEIGHT:
            add(0.0, y, z)
            add(WIDTH, y, z)
            z += step
        y += step
    x = 0.0
    while x <= WIDTH:
        z = 0.0
        while z <= HEIGHT:
            add(x, 0.0, z)
            add(x, DEPTH, z)
            z += step
        x += step
    for min_x, min_y, max_x, max_y in _BOXES:
        edge = min_x
        while edge <= max_x:
            z = 0.0
            while z <= HEIGHT:
                add(edge, min_y, z)
                add(edge, max_y, z)
                z += step
            edge += step
        edge = min_y
        while edge <= max_y:
            z = 0.0
            while z <= HEIGHT:
                add(min_x, edge, z)
                add(max_x, edge, z)
                z += step
            edge += step
    return points


_SURFACE = _surface()


def pose_at(t: float) -> dict:
    spans = []
    total = 0.0
    pairs = list(zip(_LOOP, _LOOP[1:] + _LOOP[:1]))
    for start, end in pairs:
        span = math.hypot(end[0] - start[0], end[1] - start[1])
        spans.append((total, span, start, end))
        total += span
    distance = (t * SPEED) % total
    for base, span, start, end in spans:
        if distance <= base + span or end == pairs[-1][1]:
            ratio = 0.0 if span == 0 else (distance - base) / span
            ratio = min(1.0, max(0.0, ratio))
            x = start[0] + (end[0] - start[0]) * ratio
            y = start[1] + (end[1] - start[1]) * ratio
            yaw = math.atan2(end[1] - start[1], end[0] - start[0])
            return {"x": x, "y": y, "yaw": yaw, "t": t}
    start = _LOOP[0]
    return {"x": start[0], "y": start[1], "yaw": 0.0, "t": t}


def _wrap(delta: float) -> float:
    while delta > math.pi:
        delta -= 2 * math.pi
    while delta < -math.pi:
        delta += 2 * math.pi
    return delta


def imu_at(t: float) -> dict:
    now = pose_at(t)
    nxt = pose_at(t + 0.05)
    return {
        "angular_velocity": {"x": 0.0, "y": 0.0, "z": _wrap(nxt["yaw"] - now["yaw"]) / 0.05},
        "linear_acceleration": {"x": 0.0, "y": 0.0, "z": 1.0},
    }


def sample_cloud(t: float, n: int) -> list[tuple[float, float, float]]:
    pose = pose_at(t)
    facing = (math.cos(pose["yaw"]), math.sin(pose["yaw"]))
    chosen = []
    for x, y, z in _SURFACE:
        dx = x - pose["x"]
        dy = y - pose["y"]
        if dx * facing[0] + dy * facing[1] <= 0.3:
            continue
        if dx * dx + dy * dy + (z - 1.2) ** 2 > 144:
            continue
        chosen.append((x, y, z))
    if len(chosen) <= n:
        return chosen
    stride = max(1, len(chosen) // n)
    return chosen[::stride][:n]


def _ray_segment(ox, oy, dx, dy, x1, y1, x2, y2, tmax):
    rx, ry = x2 - x1, y2 - y1
    den = dx * ry - dy * rx
    if abs(den) < 1e-9:
        return None
    t = ((x1 - ox) * ry - (y1 - oy) * rx) / den
    u = ((x1 - ox) * dy - (y1 - oy) * dx) / den
    if 0.05 < t <= tmax and 0.0 <= u <= 1.0:
        return t
    return None


def _ray_box(ox, oy, dx, dy, box, tmax):
    min_x, min_y, max_x, max_y = box
    best = None
    for x1, y1, x2, y2 in (
        (min_x, min_y, max_x, min_y),
        (max_x, min_y, max_x, max_y),
        (max_x, max_y, min_x, max_y),
        (min_x, max_y, min_x, min_y),
    ):
        hit = _ray_segment(ox, oy, dx, dy, x1, y1, x2, y2, tmax)
        if hit is not None and (best is None or hit < best):
            best = hit
    return best


def scan_2d(pose: dict, mount: dict, angles) -> list[float]:
    yaw = float(pose["yaw"])
    origin_x = pose["x"] + math.cos(yaw) * mount["x"] - math.sin(yaw) * mount["y"]
    origin_y = pose["y"] + math.sin(yaw) * mount["x"] + math.cos(yaw) * mount["y"]
    sensor_yaw = yaw + float(mount["yaw"])
    ranges = []
    for angle in angles:
        direction = sensor_yaw + float(angle)
        dx, dy = math.cos(direction), math.sin(direction)
        best = None
        for wall in _WALLS:
            hit = _ray_segment(origin_x, origin_y, dx, dy, *wall, 30.0)
            if hit is not None and (best is None or hit < best):
                best = hit
        for box in _BOXES:
            hit = _ray_box(origin_x, origin_y, dx, dy, box, 30.0)
            if hit is not None and (best is None or hit < best):
                best = hit
        ranges.append(best if best is not None else math.inf)
    return ranges
