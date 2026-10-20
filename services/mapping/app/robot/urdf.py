"""只接受 fixed 关节链，求 tracking 坐标系下的传感器外参。"""
import hashlib
import math
import xml.etree.ElementTree as ET
from pathlib import Path

from ..errors import MappingError
from ..geom import compose, identity, invert, rpy_to_quat


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def load_urdf(path: Path) -> dict:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    root = ET.fromstring(raw)
    links = set()
    joints = []
    for el in root.iter():
        if _local(el.tag) == "link" and el.get("name"):
            links.add(el.get("name"))
        if _local(el.tag) != "joint":
            continue
        parent = child = None
        xyz = (0.0, 0.0, 0.0)
        rpy = (0.0, 0.0, 0.0)
        for child_el in el:
            name = _local(child_el.tag)
            if name == "parent":
                parent = child_el.get("link")
            elif name == "child":
                child = child_el.get("link")
            elif name == "origin":
                xyz = _nums(child_el.get("xyz"), 3)
                rpy = _nums(child_el.get("rpy"), 3)
        joints.append({
            "name": el.get("name") or "",
            "type": (el.get("type") or "fixed").strip(),
            "parent": parent,
            "child": child,
            "xyz": xyz,
            "rpy": rpy,
        })
    return {"sha256": digest, "links": links, "joints": joints, "path": str(path)}


def transform(model: dict, tracking: str, frame: str) -> dict:
    frame = frame.lstrip("/")
    tracking = tracking.lstrip("/")
    if frame == tracking:
        return identity()
    if frame not in model["links"] or tracking not in model["links"]:
        raise MappingError("MAPPING_CALIBRATION_MISSING", f"URDF 中没有坐标系 {frame} 或 {tracking}")
    graph: dict[str, list] = {}
    for joint in model["joints"]:
        graph.setdefault(joint["parent"], []).append(joint)
        graph.setdefault(joint["child"], []).append(joint)
    # parent_T_child along each edge, plus the reverse
    edges: dict[tuple[str, str], tuple[str, dict]] = {}
    for joint in model["joints"]:
        tf = {
            "translation": {"x": joint["xyz"][0], "y": joint["xyz"][1], "z": joint["xyz"][2]},
            "rotation": rpy_to_quat(*joint["rpy"]),
        }
        edges[(joint["parent"], joint["child"])] = (joint["type"], tf)
        edges[(joint["child"], joint["parent"])] = (joint["type"], invert(tf))
    came = {tracking: None}
    queue = [tracking]
    while queue:
        node = queue.pop(0)
        if node == frame:
            break
        for nxt in graph.get(node, []):
            other = nxt["child"] if nxt["parent"] == node else nxt["parent"]
            if other not in came:
                came[other] = node
                queue.append(other)
    if frame not in came:
        raise MappingError("MAPPING_CALIBRATION_MISSING", f"URDF 中 {tracking} 到 {frame} 不连通")
    chain = []
    cursor = frame
    while cursor != tracking:
        prev = came[cursor]
        chain.append((prev, cursor))
        cursor = prev
    chain.reverse()
    out = identity()
    for parent, child in chain:
        kind, tf = edges[(parent, child)]
        if kind != "fixed":
            raise MappingError("MAPPING_CALIBRATION_MISSING", f"{parent} 到 {child} 不是固定关节")
        out = compose(out, tf)
    for key in ("x", "y", "z", "w"):
        if not math.isfinite(out["rotation"][key]):
            raise MappingError("MAPPING_CALIBRATION_MISSING", "外参无效")
    return out


def _nums(text: str | None, n: int) -> tuple:
    parts = (text or "").split()
    if len(parts) != n:
        return tuple(0.0 for _ in range(n))
    return tuple(float(p) for p in parts)
