"""话术渲染与播报目标路由（对齐 speak.ts 的 render / resolveTarget）。

render：读 config/utterances.zh-CN.json，{n}/{goal} 等占位符逐参数 str.replace
（缺失参数不炸：模板中未提供的占位符原样保留，key 不存在时返回 key 本身）。
resolveTarget：wake→vehicle；query→speak.query；cabin→speak.cabinMove；ptt→speak.pttMove。
"""
import json

from .config import CORE_ROOT

_TEMPLATES_PATH = CORE_ROOT / "config" / "utterances.zh-CN.json"

with open(_TEMPLATES_PATH, "r", encoding="utf-8") as _f:
    TEMPLATES: dict = json.load(_f)


def render(key: str, params: dict | None = None) -> str:
    text = TEMPLATES.get(key, key)
    for k, v in (params or {}).items():
        text = text.replace("{" + str(k) + "}", str(v))
    return text


def resolve_target(channel: str, speak_kind: str, cfg: dict) -> str:
    if speak_kind == "wake":
        return "vehicle"
    if speak_kind == "query":
        return cfg["speak"]["query"]
    if channel == "cabin":
        return cfg["speak"]["cabinMove"]
    return cfg["speak"]["pttMove"]
