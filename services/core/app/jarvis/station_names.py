"""口语站点名 → 地图 PathPoint 名。

地图点常见为 p1/p4（小写、不带「点」）。口语「P1点」若原样下发，真车对不上会空跑。
解析时剥尾部「点/站点」；下发前再按 /api/map 的 PathPoint 做大小写不敏感匹配。
无地图时：纯 ASCII 站名（P1、A）改小写。
"""
from __future__ import annotations

import re

_ASCII_STATION = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_STATION_KEYS = ("start_name", "target_name", "goal")


def strip_spoken_station_suffix(name: str) -> str:
    s = str(name or "").strip()
    if not s:
        return s
    if s.endswith("站点") and len(s) > 2:
        return s[:-2]
    if s.endswith("点") and len(s) > 1:
        return s[:-1]
    return s


def _variants(name: str) -> list[str]:
    raw = str(name or "").strip()
    if not raw:
        return []
    out: list[str] = []
    for v in (raw, strip_spoken_station_suffix(raw)):
        if v and v not in out:
            out.append(v)
    return out


def extract_path_point_names(payload) -> list[str]:
    """从 Jarvis GET /api/map JSON 取 PathPoint.name。结构因车型略有差异。"""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        data = payload
    names: list[str] = []

    def _add(n) -> None:
        if n is None:
            return
        s = str(n).strip()
        if s and s not in names:
            names.append(s)

    objs = data.get("Objs") or data.get("objs")
    if isinstance(objs, dict):
        pts = objs.get("PathPoint") or objs.get("pathPoint") or []
        if isinstance(pts, list):
            for p in pts:
                if isinstance(p, dict):
                    _add(p.get("name"))

    path_points = data.get("path_points") or payload.get("path_points")
    if isinstance(path_points, dict):
        points = path_points.get("points") or []
        if isinstance(points, list):
            for p in points:
                if isinstance(p, dict):
                    _add(p.get("name") or p.get("id"))
                elif isinstance(p, str):
                    _add(p)
    return names


def resolve_station_name(name: str, map_names: list[str] | None = None) -> str:
    """把口语站名收成地图名。匹配失败时返回剥后缀后的启发式结果。"""
    raw = str(name or "").strip()
    if not raw:
        return raw
    variants = _variants(raw)
    if map_names:
        exact = {n: n for n in map_names if n}
        folded = {n.casefold(): n for n in map_names if n}
        for v in variants:
            if v in exact:
                return exact[v]
        for v in variants:
            hit = folded.get(v.casefold())
            if hit:
                return hit
    bare = variants[-1] if variants else raw
    if _ASCII_STATION.fullmatch(bare):
        return bare.lower()
    return bare


def resolve_station_fields(params: dict, map_names: list[str] | None = None) -> dict:
    """复制 params，对起止点/goal（非 auto）做站点归一。不改入参。"""
    out = dict(params or {})
    for key in _STATION_KEYS:
        v = out.get(key)
        if v is None or v == "" or v == "auto":
            continue
        out[key] = resolve_station_name(str(v), map_names)
    return out
