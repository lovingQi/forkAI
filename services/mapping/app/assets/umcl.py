"""UMCL 校验，规则对齐 robospace contracts.ts isUmclJsonMap。"""


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and abs(value) != float("inf")


def _safe_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2**53 - 1


def is_umcl_json_map(value) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("Header") not in ("umcl-map", "umcl-map3"):
        return False
    if not _finite(value.get("MapRes")) or value["MapRes"] <= 0:
        return False
    if not isinstance(value.get("MinPose"), str) or not isinstance(value.get("MaxPose"), str):
        return False
    if not _safe_int(value.get("NumPoints")) or not isinstance(value.get("ObsPoints"), list):
        return False
    point_count = 0
    for column in value["ObsPoints"]:
        if not isinstance(column, list) or len(column) < 2 or any(not _finite(item) for item in column):
            return False
        point_count += len(column) - 1
        if point_count > 2**53 - 1:
            return False
    if point_count != value["NumPoints"]:
        return False
    if "NumLines" in value and not _safe_int(value["NumLines"]):
        return False
    if "Lines" in value and not isinstance(value["Lines"], list):
        return False
    objs = value.get("Objs", None)
    if objs is not None and not isinstance(objs, (list, dict)):
        return False
    return True
