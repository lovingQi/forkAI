"""任务流名称模糊匹配（步骤47）。

match_flow_name(query, flows) -> (best, candidates)：
- norm（去空格标点转小写）后：完全相等 score=1.0；
  name 包含 query 或 query 包含 name → 0.8；
  否则纯 Python 编辑距离相似度 ratio
- 阈值 0.6；返回 top1 + 候选列表（≤3，按分降序）；无匹配返回 (None, [])
flows 项为 dict（含 id/name）或 (id, name) 元组均可。
"""
import re

THRESHOLD = 0.6
MAX_CANDIDATES = 3


def _norm(s: str) -> str:
    return re.sub(r"[\s，,。.!！？?]", "", str(s or "")).lower()


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _ratio(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    dist = _levenshtein(a, b)
    return 1.0 - dist / max(len(a), len(b))


def _score(query: str, name: str) -> float:
    if query == name:
        return 1.0
    if query and name and (query in name or name in query):
        return 0.8
    return _ratio(query, name)


def match_flow_name(query: str, flows: list) -> tuple:
    q = _norm(query)
    if not q:
        return None, []
    scored = []
    for f in flows:
        fid = f.get("id") if isinstance(f, dict) else f[0]
        name = f.get("name") if isinstance(f, dict) else f[1]
        s = _score(q, _norm(name))
        if s >= THRESHOLD:
            scored.append({"id": fid, "name": name, "score": s})
    scored.sort(key=lambda x: -x["score"])
    candidates = scored[:MAX_CANDIDATES]
    best = candidates[0] if candidates else None
    return best, candidates
