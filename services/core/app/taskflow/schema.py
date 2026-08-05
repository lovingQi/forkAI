"""FlowJSON 结构定义与校验（步骤35）。

结构：
    {
      "id": "uuid", "name": "标准取货流程",
      "nodes": [{"id": "n1", "type": "focklift|head|follow_back|get_pallet|charge|drive",
                 "params": {...}}],
      "edges": [{"from": "n1", "to": "n2", "on": "success|fail"}],
      "parallel_groups": [["n3", "n4"]],           # 可选
      "options": {"node_timeout_s": 120}            # 可选，覆盖 config taskflow 段
    }

- drive 节点（流程内定时点动）：params {trans, rot, speed, duration_s}，
  直接 jarvis.control('drive') → sleep(duration) → control('stop')，不走 watchdog
- 入口节点 = 无入边的节点（必须恰好 1 个，并行组视作整体）
"""
from ..tasks.schemas import TASK_SCHEMAS

NODE_TYPES = {"focklift", "head", "follow_back", "get_pallet", "charge", "drive"}

# route cmd → tasks/schemas.py 的意图名（复用其 required 参数定义）
_CMD_INTENT = {
    "head": "TASK_HEAD",
    "follow_back": "TASK_FOLLOW_BACK",
    "get_pallet": "TASK_GET_PALLET",
    "charge": "TASK_CHARGE",
}
_FOCKLIFT_REQUIRED = ["pos"]


def validate(flow) -> list:
    """返回错误列表（空 = 合法）。"""
    errors: list = []
    if not isinstance(flow, dict):
        return ["flow 必须是对象"]
    nodes = flow.get("nodes")
    edges = flow.get("edges") or []
    groups = flow.get("parallel_groups") or []
    if not isinstance(nodes, list) or not nodes:
        return ["nodes 不能为空"]
    if not isinstance(edges, list):
        return ["edges 必须是数组"]
    if not isinstance(groups, list):
        return ["parallel_groups 必须是数组"]

    ids: list = []
    for n in nodes:
        if not isinstance(n, dict):
            errors.append("节点必须是对象")
            continue
        nid, ntype = n.get("id"), n.get("type")
        params = n.get("params") or {}
        if not nid:
            errors.append("节点缺 id")
            continue
        if nid in ids:
            errors.append(f"节点 id 重复: {nid}")
        ids.append(nid)
        if ntype not in NODE_TYPES:
            errors.append(f"{nid}: 未知节点类型 {ntype!r}")
            continue
        if ntype == "drive":
            if params.get("duration_s") is None:
                errors.append(f"{nid}: drive 缺 required 参数 duration_s")
        elif ntype == "focklift":
            for p in _FOCKLIFT_REQUIRED:
                if params.get(p) is None:
                    errors.append(f"{nid}: focklift 缺 required 参数 {p}")
        else:
            schema = TASK_SCHEMAS[_CMD_INTENT[ntype]]
            for pname, spec in schema["params"].items():
                if spec.get("required") and params.get(pname) is None:
                    errors.append(f"{nid}: 缺 required 参数 {pname}")

    idset = set(ids)
    out_count: dict = {}
    for e in edges:
        if not isinstance(e, dict):
            errors.append("边必须是对象")
            continue
        f, t, on = e.get("from"), e.get("to"), e.get("on")
        if f not in idset:
            errors.append(f"边 from 引用不存在: {f}")
        if t not in idset:
            errors.append(f"边 to 引用不存在: {t}")
        if on not in ("success", "fail"):
            errors.append(f"边 on 非法（须 success|fail）: {on}")
            continue
        key = (f, on)
        out_count[key] = out_count.get(key, 0) + 1
        if out_count[key] > 1:
            errors.append(f"节点 {f} 的 {on} 出边多于一条")

    member_of = _group_map(groups, idset, errors)
    units, adj, _members, _entry = build_units(ids, edges, member_of)
    if len(_entry) != 1:
        errors.append(f"入口节点必须恰好1个（并行组视作整体），当前 {len(_entry)} 个: {sorted(_entry)}")
    cycles = _find_cycles(units, adj)
    if cycles:
        errors.append(f"存在环: {cycles}")
    return errors


def _group_map(groups: list, idset: set, errors: list) -> dict:
    """节点 id → 组序号；校验引用存在且不重叠。"""
    member_of: dict = {}
    for gi, g in enumerate(groups):
        if not isinstance(g, list):
            errors.append(f"并行组 {gi} 必须是数组")
            continue
        for m in g:
            if m not in idset:
                errors.append(f"并行组成员不存在: {m}")
                continue
            if m in member_of:
                errors.append(f"并行组成员重叠: {m}")
                continue
            member_of[m] = gi
    return member_of


def build_units(ids: list, edges: list, member_of: dict):
    """把并行组缩成整体单元。

    返回 (units, adj, members, entries)：
    - units: 单元 key 列表（单节点=节点 id；并行组="g{序号}"）
    - adj: {unit: {"success": unit|None, "fail": unit|None}}（组级别取成员边的第一条）
    - members: {unit: [节点 id,...]}
    - entries: 无入边的单元集合
    """
    def unit_of(nid: str) -> str:
        return f"g{member_of[nid]}" if nid in member_of else nid

    units: list = []
    members: dict = {}
    for nid in ids:
        u = unit_of(nid)
        if u not in members:
            units.append(u)
            members[u] = []
        members[u].append(nid)

    adj: dict = {u: {"success": None, "fail": None} for u in units}
    indeg: dict = {}
    for e in edges:
        a, b = unit_of(e["from"]), unit_of(e["to"])
        if a == b:
            continue
        on = e.get("on")
        if on in ("success", "fail") and adj[a][on] is None:
            adj[a][on] = b
        indeg[b] = indeg.get(b, 0) + 1
    entries = {u for u in units if indeg.get(u, 0) == 0}
    return units, adj, members, entries


def _find_cycles(units: list, adj: dict) -> list:
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {u: WHITE for u in units}
    cycles: list = []

    def dfs(u: str) -> None:
        color[u] = GRAY
        for on in ("success", "fail"):
            v = adj[u][on]
            if v is None:
                continue
            if color.get(v) == GRAY:
                cycles.append(f"{u}->{v}")
            elif color.get(v) == WHITE:
                dfs(v)
        color[u] = BLACK

    for u in units:
        if color[u] == WHITE:
            dfs(u)
    return cycles
