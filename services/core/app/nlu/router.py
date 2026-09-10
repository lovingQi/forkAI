"""混合 NLU 路由：规则快路径（可关）→ 云端 LLM；失败不回落规则首命中。

parse_intent(text, llm, cfg) -> list[dict]：
- 无否定的 STOP（停/停止/急停/停下/停车）永远走规则立刻停车
- nlu.rules_enabled 为 true 时，同时满足才跳过 LLM：
  ① 整句只命中一个规则意图
  ② 去掉语气词/连接词后无剩余实词
  ③ 无否定/纠正（不、别、不要、不是、改成、还是）
- 关规则快路径时，非 STOP 句全走 LLM
- LLM 成功则只返回其白名单列表（可为空 []，静默不执行）
- LLM 超时/挂掉/非 JSON → [{"name": "LLM_FAIL", ...}]，整句不执行
"""
import re

from .llm import LLMClient
from .prompts import INTENT_NAMES
from .rules import norm, parse_intent_rule

LLM_FAIL = "LLM_FAIL"

_CONNECTOR_RE = re.compile(r"然后|接着|随后|之后|并且|而且|顺便|先|再|又|就|，|,")
_FILLER_RE = re.compile(r"啊+|吧+|呀+|哦+|呃+|嗯+|哎+|一下")
_NEGATION_RE = re.compile(r"不要|不是|别|不|改成|还是")

_SLOT_REQUIRED = {
    "TASK_HEAD": {"angle": (int, float)},
    "FORK_LIFT_TO": {"n": (int, float)},
    "SPEED_SET": {"n": (int, float)},
    "GOTO_GOAL": {"goal": str},
    "FLOW_START": {"name": str},
}


def clamp_max_intents(n) -> int:
    try:
        v = int(n)
    except (TypeError, ValueError):
        v = 5
    return max(1, min(8, v))


def _slots_sane(name: str, slots: dict) -> bool:
    required = _SLOT_REQUIRED.get(name)
    if not required:
        return True
    for key, types in required.items():
        v = slots.get(key)
        if isinstance(v, bool) or not isinstance(v, types):
            return False
        if isinstance(v, str) and not v.strip():
            return False
    return True


def has_negation(text: str) -> bool:
    return bool(_NEGATION_RE.search(norm(text)))


def _strip_fillers(text: str) -> str:
    return _FILLER_RE.sub("", _CONNECTOR_RE.sub("", text)).strip()


def residual_after_first(text: str, first: dict) -> str:
    """去掉首个规则命中区间、语气词与连接词后的残余。"""
    normed = norm(text)
    s, e = first.get("span", (0, 0))
    return _strip_fillers(normed[:s] + normed[e:])


def leftover_needs_llm(text: str, first: dict) -> bool:
    """残余为空，或残余仍只是同一意图，则不需要因残余走 LLM。"""
    residual = residual_after_first(text, first)
    if not residual:
        return False
    nxt = parse_intent_rule(residual)
    if nxt["name"] == first["name"]:
        ns, ne = nxt.get("span", (0, 0))
        rest = _strip_fillers(residual[:ns] + residual[ne:])
        return bool(rest)
    return True


def is_immediate_stop(text: str) -> bool:
    if has_negation(text):
        return False
    return parse_intent_rule(text)["name"] == "STOP"


def rules_enabled(cfg: dict | None) -> bool:
    nlu = (cfg or {}).get("nlu") or {}
    return bool(nlu.get("rules_enabled", True))


def rule_route(text: str, cfg: dict | None = None) -> tuple[str, list]:
    """规则门限。返回 (via, intents)，via ∈ rule | need_llm。"""
    first = parse_intent_rule(text)
    if is_immediate_stop(text):
        return "rule", [first]
    if not rules_enabled(cfg):
        return "need_llm", [first]
    if first["name"] == "UNKNOWN" or has_negation(text) or leftover_needs_llm(text, first):
        return "need_llm", [first]
    return "rule", [first]


def sanitize_slots(name: str, slots: dict, cfg: dict | None) -> dict:
    """LLM 数值事后校正：高度毫米（n≤10 视为米）、叉高/速度夹紧。不改入参。"""
    out = dict(slots or {})
    cfg = cfg or {}
    if name == "FORK_LIFT_TO":
        n = out.get("n")
        if isinstance(n, bool) or not isinstance(n, (int, float)):
            return out
        if n <= 10:
            n = n * 1000
        n = int(round(n))
        fork = cfg.get("fork") or {}
        lo = int(fork.get("min_pos", 75))
        hi = int(fork.get("max_pos", 210))
        out["n"] = max(lo, min(hi, n))
    elif name == "SPEED_SET":
        n = out.get("n")
        if isinstance(n, bool) or not isinstance(n, (int, float)):
            return out
        n = int(round(n))
        hi = int((cfg.get("speed") or {}).get("max", 40))
        out["n"] = max(5, min(hi, n))
    return out


def _fail(text: str) -> list:
    return [{"name": LLM_FAIL, "slots": {}, "raw_text": text}]


def _log(via: str, text: str, names: list[str]) -> None:
    preview = text if len(text) <= 40 else text[:40] + "…"
    print(f"[forkai-core] nlu via={via} text={preview!r} intents={names}", flush=True)


async def parse_intent(text: str, llm: LLMClient | None, cfg: dict | None = None) -> list:
    cfg = cfg if cfg is not None else (getattr(llm, "_cfg", None) if llm is not None else {})
    via, intents = rule_route(text, cfg)
    if via == "rule":
        _log("rule", text, [i["name"] for i in intents])
        return intents

    if llm is None:
        _log("llm_fail", text, [LLM_FAIL])
        return _fail(text)

    extracted = await llm.extract(text)
    if extracted is None:
        _log("llm_fail", text, [LLM_FAIL])
        return _fail(text)

    max_n = clamp_max_intents((cfg or {}).get("nlu", {}).get("max_intents", 5))
    valid = []
    for e in extracted:
        name = e.get("intent")
        slots = e.get("slots") if isinstance(e.get("slots"), dict) else {}
        if name not in INTENT_NAMES or name == LLM_FAIL:
            continue
        if not _slots_sane(name, slots):
            continue
        valid.append({
            "name": name,
            "slots": sanitize_slots(name, slots, cfg),
            "raw_text": text,
        })
        if len(valid) >= max_n:
            break

    if not valid:
        _log("llm_empty", text, [])
        return []
    _log("llm", text, [i["name"] for i in valid])
    return valid
