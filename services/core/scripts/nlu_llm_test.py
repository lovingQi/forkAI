#!/usr/bin/env python3
"""混合 NLU 直测（步骤26验证）：10 条用例过 router.parse_intent。

- 规则命中 5 条：不应经过 LLM（打印 via=rule）
- 复杂 5 条：规则 UNKNOWN → LLM，打印 LLM 原始输出与最终意图列表

前置：llama-server 已在 :19002 运行（测 LLM 用例需要；不在则全部降级 UNKNOWN）。

用法（services/core 目录下）：
    .venv/bin/python scripts/nlu_llm_test.py
"""
import asyncio
import json
import sys
from pathlib import Path

import httpx

CORE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CORE_ROOT))

from app.config import load_config  # noqa: E402
from app.nlu.llm import LLMClient  # noqa: E402
from app.nlu.router import parse_intent, rule_route  # noqa: E402

RULE_CASES = ["前进", "停止", "电量多少", "升到150毫米", "去A区"]
LLM_CASES = [
    "升到2米然后去A区",
    "先回充再空闲",
    "把货叉放下去充电",
    "往左挪一点再前进",
    "今天电量怎么样还能干活吗",
]

# 期望（仅用于对照打印，不断言——0.5B 表现需人工评估）
EXPECTED = {
    "升到2米然后去A区": [("FORK_LIFT_TO", {"n": 2000}), ("GOTO_GOAL", {"goal": "A区"})],
    "先回充再空闲": [("DOCK", {}), ("IDLE", {})],
    "把货叉放下去充电": [("FORK_LIFT_DOWN", {}), ("DOCK", {})],
    "往左挪一点再前进": [("TURN_LEFT", {}), ("MOVE_FWD", {})],
    "今天电量怎么样还能干活吗": [("QUERY_BATTERY", {})],
}


async def llm_raw(cfg: dict, text: str) -> str:
    """直调 /v1/chat/completions 拿原始输出（用于报告 LLM 实际表现）。"""
    from app.nlu.prompts import SYSTEM_PROMPT

    base = cfg["llm"]["base_url"].rstrip("/")
    async with httpx.AsyncClient(timeout=float(cfg["llm"].get("timeout_s", 3)) * 3) as c:
        res = await c.post(
            f"{base}/v1/chat/completions",
            json={
                "model": "test",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                "temperature": 0,
                "max_tokens": 256,
            },
        )
        return res.json()["choices"][0]["message"]["content"]


async def main() -> int:
    cfg = load_config()
    llm = LLMClient(cfg)

    print("==== 规则命中用例（不应经过 LLM）====")
    for text in RULE_CASES:
        via, _ = rule_route(text)
        intents = await parse_intent(text, llm)
        got = [(i["name"], i["slots"]) for i in intents]
        print(f"  {text!r:14} via={via:8} → {got}")

    print("==== 复杂用例（compound/unknown → LLM）====")
    for text in LLM_CASES:
        via, _ = rule_route(text)
        raw = ""
        if via != "rule":
            try:
                raw = await llm_raw(cfg, text)
            except Exception as e:
                raw = f"<请求失败: {e}>"
        intents = await parse_intent(text, llm)
        got = [(i["name"], i["slots"]) for i in intents]
        exp = EXPECTED.get(text)
        mark = "✓" if exp is not None and got == exp else "✗"
        print(f"  {text!r} via={via}")
        print(f"    LLM原始输出: {raw.strip()[:200]!r}")
        print(f"    最终意图: {got}  期望: {exp}  {mark}")

    await llm.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
