"""混合 NLU 路由（步骤26）：规则 → LLM → UNKNOWN。

parse_intent(text, llm) -> list[dict]：
- rule_route 先跑规则：
  - 规则命中且残余文本不再含其他意图（via=rule）→ [该意图]，不经过 LLM
  - 规则命中但残余文本仍含意图（via=compound，复合指令）→ 交 LLM 拆分
  - 规则 UNKNOWN（via=unknown）→ 交 LLM
- LLM 非空则做映射校验（不在 INTENT_NAMES 集合内的项丢弃），返回标准意图 dict 列表
- LLM 不可用/失败/全被丢弃 → 降级：unknown 返回 [UNKNOWN]；compound 返回首个规则命中
  （部分执行，与"llm.enabled=false 时行为与现状一致"兼容）
"""
import re

from .llm import LLMClient
from .prompts import INTENT_NAMES
from .rules import norm, parse_intent_rule

# 复合连接词：从残余文本中剔除后再判断是否仍含意图
_CONNECTOR_RE = re.compile(r"然后|接着|随后|之后|并且|而且|顺便|先|再|又|就|，|,")


def rule_route(text: str) -> tuple[str, list]:
    """规则路由。返回 (via, intents)，via ∈ rule | compound | unknown。"""
    first = parse_intent_rule(text)
    if first["name"] == "UNKNOWN":
        return "unknown", [first]
    normed = norm(text)
    s, e = first.get("span", (0, 0))
    residual = _CONNECTOR_RE.sub("", normed[:s] + normed[e:])
    if residual and parse_intent_rule(residual)["name"] != "UNKNOWN":
        # 残余里还有别的意图 → 复合指令，交 LLM 拆分
        return "compound", [first]
    return "rule", [first]


async def parse_intent(text: str, llm: LLMClient | None) -> list:
    via, intents = rule_route(text)
    if via == "rule":
        return intents
    if llm is not None:
        extracted = await llm.extract(text)
        if extracted:
            valid = [
                {"name": e["intent"], "slots": e["slots"], "raw_text": text}
                for e in extracted
                if e["intent"] in INTENT_NAMES
            ]
            if valid:
                # 单位换算以小模型为弱项：LLM 首个意图与规则首个命中同名且规则带数值
                # slot（FORK_LIFT_TO/SPEED_SET 的 n）时，用规则解析的 n 覆盖（规则换算
                # 毫米/厘米/米/百分比是确定性的，0.5B 常把 150毫米 误算成 1500）
                first = intents[0]
                if (
                    valid[0]["name"] == first["name"]
                    and isinstance(first["slots"].get("n"), int)
                ):
                    valid[0]["slots"]["n"] = first["slots"]["n"]
                return valid[:3]
    return intents
