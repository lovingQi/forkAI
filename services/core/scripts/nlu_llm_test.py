#!/usr/bin/env python3
"""混合 NLU 直测：规则快路径 + 云端 LLM。

- 规则快路径：短指令不应经过 LLM（打印 via=rule）
- 否定/复合/残余实词：交 LLM；打印 LLM 原始输出与最终意图
- 人工评估项：否定句不得含被否动作；空列表；单位毫米

前置：FORKAI_TTS_API_KEY（SiliconFlow）与所选 LLM；官方 DeepSeek 项另需 FORKAI_DEEPSEEK_API_KEY。

用法（services/core 目录下）：
    .venv/bin/python scripts/nlu_llm_test.py
"""
import asyncio
import sys
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CORE_ROOT))

from app.config import load_config  # noqa: E402
from app.nlu.llm import LLMClient  # noqa: E402
from app.nlu.router import parse_intent, rule_route  # noqa: E402

RULE_CASES = ["前进", "停止", "加速", "升到150毫米", "去A区"]
LLM_CASES = [
    "升到2米然后去A区",
    "先回充再空闲",
    "把货叉放下去充电",
    "往左挪一点再前进",
    "后退不要前进",
    "不要前进",
    "今天电量怎么样还能干活吗",
    "原地旋转90度，再从P1点去P4点",
]

# 期望（对照打印；云端大模型仍建议人工扫一眼）
# 升到2米：LLM 应出 n=2000，事后夹紧到 fork.max_pos（默认 210）
EXPECTED = {
    "升到2米然后去A区": [("FORK_LIFT_TO", {"n": 210}), ("GOTO_GOAL", {"goal": "A区"})],
    "先回充再空闲": [("DOCK", {}), ("IDLE", {})],
    "把货叉放下去充电": [("FORK_LIFT_DOWN", {}), ("DOCK", {})],
    "往左挪一点再前进": [("TURN_LEFT", {}), ("MOVE_FWD", {})],
    "后退不要前进": [("MOVE_BACK", {})],
    "不要前进": [],
    "今天电量怎么样还能干活吗": [("QUERY_BATTERY", {})],
    "原地旋转90度，再从P1点去P4点": [
        ("TASK_HEAD", {"angle": 90}),
        ("TASK_FOLLOW_BACK", {"start_name": "p1", "target_name": "p4"}),
    ],
}


async def llm_raw(llm: LLMClient, text: str) -> str:
    """直调当前配置的云端模型拿原始输出。"""
    raw = await llm.raw_content(text)
    return raw if raw is not None else "<请求失败或未配置 key>"


async def main() -> int:
    cfg = load_config()
    llm = LLMClient(cfg)

    print("==== 规则快路径（不应经过 LLM）====")
    for text in RULE_CASES:
        via, _ = rule_route(text, cfg)
        intents = await parse_intent(text, llm, cfg)
        got = [(i["name"], i["slots"]) for i in intents]
        print(f"  {text!r:14} via={via:8} → {got}")

    print("==== 需 LLM（否定/复合/残余）人工评估：被否动作不得出现 ====")
    for text in LLM_CASES:
        via, _ = rule_route(text, cfg)
        raw = ""
        if via != "rule":
            try:
                raw = await llm_raw(llm, text)
            except Exception as e:
                raw = f"<请求失败: {e}>"
        intents = await parse_intent(text, llm, cfg)
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
