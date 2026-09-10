#!/usr/bin/env python3
"""NLU 黄金语料集回归（方案E）：防规则/纠偏表改动引入回归。

纯规则层测试（parse_intent_rule + correct_asr），不依赖 LLM/服务，可离线秒级跑完。
每条语料：输入文本 → 期望意图名 + 关键 slot 断言。

用法（在 services/core 目录下）：
    .venv/bin/python scripts/nlu_corpus_test.py           # 全量跑
    .venv/bin/python scripts/nlu_corpus_test.py -v        # 打印每条明细
退出码：0=全过，1=有失败。
"""
import sys
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CORE_ROOT))

from app.nlu.rules import correct_asr, parse_intent_rule  # noqa: E402
from app.nlu.router import is_immediate_stop, rule_route  # noqa: E402

# (输入, 期望意图, {slot: 期望值} 或 None)
CORPUS: list[tuple[str, str, dict | None]] = [
    # ---- 点动 ----
    ("前进", "MOVE_FWD", None),
    ("往前走", "MOVE_FWD", None),
    ("后退", "MOVE_BACK", None),
    ("倒车", "MOVE_BACK", None),
    ("左转", "TURN_LEFT", None),
    ("往左", "TURN_LEFT", None),
    ("右转", "TURN_RIGHT", None),
    ("停止", "STOP", None),
    ("停下", "STOP", None),
    ("急停", "STOP", None),
    # ---- 调速 ----
    ("快点", "SPEED_UP", None),
    ("加速", "SPEED_UP", None),
    ("慢点", "SPEED_DOWN", None),
    ("减速", "SPEED_DOWN", None),
    ("速度调到35", "SPEED_SET", {"n": 35}),
    ("速度调到35%", "SPEED_SET", {"n": 35}),
    ("调到50%", "SPEED_SET", {"n": 50}),
    # ---- 货叉 ----
    ("升起货叉", "FORK_LIFT_UP", None),
    ("升叉", "FORK_LIFT_UP", None),
    ("放下货叉", "FORK_LIFT_DOWN", None),
    ("降叉", "FORK_LIFT_DOWN", None),
    ("升到150毫米", "FORK_LIFT_TO", {"n": 150}),
    ("升到15厘米", "FORK_LIFT_TO", {"n": 150}),
    ("升到1.5米", "FORK_LIFT_TO", {"n": 1500}),
    ("货叉调到210", "FORK_LIFT_TO", {"n": 210}),
    # ---- 旋转 ----
    ("原地转90度", "TASK_HEAD", {"angle": 90}),
    ("向左转45度", "TASK_HEAD", {"angle": 45}),
    ("向右转90度", "TASK_HEAD", {"angle": -90}),
    ("掉头", "TASK_HEAD", {"angle": 180}),
    # ---- 任务 ----
    ("从A点到B点", "TASK_FOLLOW_BACK", {"start_name": "A点", "target_name": "B点"}),
    ("从从A点到B点", "TASK_FOLLOW_BACK", {"start_name": "A点", "target_name": "B点"}),
    ("从1号区去2号区", "TASK_FOLLOW_BACK", {"start_name": "1号区", "target_name": "2号区"}),
    ("盲叉取货", "TASK_FOLLOW_BACK", {"get_pallet": True}),
    ("识别栈板", "TASK_GET_PALLET", None),
    ("自动取货", "TASK_GET_PALLET", None),
    ("栈板取货", "TASK_GET_PALLET", None),
    ("去充电", "DOCK", None),
    ("回充", "DOCK", None),
    ("去1号充电桩充电", "TASK_CHARGE", {"goal": "1号充电桩"}),
    ("空闲", "IDLE", None),
    ("待机", "IDLE", None),
    # ---- 问答 ----
    ("电量多少", "QUERY_BATTERY", None),
    ("还有多少电", "QUERY_BATTERY", None),
    ("当前模式", "QUERY_MODE", None),
    ("什么模式", "QUERY_MODE", None),
    ("位置", "QUERY_POSE", None),
    ("在哪", "QUERY_POSE", None),
    ("叉高多少", "QUERY_FORK_HEIGHT", None),
    ("货叉多高", "QUERY_FORK_HEIGHT", None),
    ("电机状态", "QUERY_MOTOR", None),
    ("速度多少", "QUERY_SPEED", None),
    ("当前速度", "QUERY_SPEED", None),
    ("当前任务", "QUERY_TASK", None),
    ("在干什么", "QUERY_TASK", None),
    ("车况", "QUERY_STATUS", None),
    ("告警", "QUERY_ALARM", None),
    ("报警", "QUERY_ALARM", None),
    ("为什么停", "QUERY_ALARM_EXPLAIN", None),
    ("怎么回事", "QUERY_ALARM_EXPLAIN", None),
    # ---- 任务流控制 ----
    ("执行取货演示流程", "FLOW_START", {"name": "取货演示"}),
    ("开始回充流程", "FLOW_START", {"name": "回充"}),
    ("暂停任务", "FLOW_PAUSE", None),
    ("继续任务", "FLOW_RESUME", None),
    ("取消任务", "FLOW_CANCEL", None),
    # ---- 对话控制 ----
    ("确认", "CONFIRM", None),
    ("是的", "CONFIRM", None),
    ("取消", "CANCEL", None),
    ("算了", "CANCEL", None),
    # ---- 纠偏表覆盖（ASR 误识别 → 纠偏后命中）----
    ("掂量多少", "QUERY_BATTERY", None),       # 掂量→电量
    ("前经", "MOVE_FWD", None),                # 前经→前进
    ("作战", "TURN_LEFT", None),               # 作战→左转
    ("识别站板", "TASK_GET_PALLET", None),     # 站板→栈板
    # ---- 未识别（应为 UNKNOWN）----
    ("今天天气怎么样", "UNKNOWN", None),
    ("你好", "UNKNOWN", None),
    ("随便说点什么", "UNKNOWN", None),
]


def run() -> int:
    verbose = "-v" in sys.argv
    passed = failed = 0
    failures: list[str] = []
    for text, want_name, want_slots in CORPUS:
        got = parse_intent_rule(correct_asr(text))
        got_name = got["name"]
        ok = got_name == want_name
        slot_err = ""
        if ok and want_slots:
            for k, v in want_slots.items():
                gv = got["slots"].get(k)
                if gv != v:
                    ok = False
                    slot_err = f"slot {k}: 期望 {v!r} 实际 {gv!r}"
                    break
        if ok:
            passed += 1
            if verbose:
                print(f"  ✓ {text!r:24} → {got_name}")
        else:
            failed += 1
            msg = f"  ✗ {text!r:24} 期望 {want_name} 实际 {got_name} {slot_err}"
            failures.append(msg)
            print(msg)

    # 规则快路径门限（不调 LLM）
    gate: list[tuple[str, str, bool]] = [
        ("前进", "rule", False),
        ("停止", "rule", True),
        ("急停", "rule", True),
        ("升到150毫米", "rule", False),
        ("后退不要前进", "need_llm", False),
        ("不要前进", "need_llm", False),
        ("不要停", "need_llm", False),
        ("升到2米然后去A区", "need_llm", False),
    ]
    for text, want_via, want_stop in gate:
        via, _ = rule_route(text)
        stop = is_immediate_stop(text)
        ok = via == want_via and stop == want_stop
        if ok:
            passed += 1
            if verbose:
                print(f"  ✓ gate {text!r:24} via={via} stop={stop}")
        else:
            failed += 1
            msg = (
                f"  ✗ gate {text!r:24} 期望 via={want_via} stop={want_stop} "
                f"实际 via={via} stop={stop}"
            )
            failures.append(msg)
            print(msg)

    total = passed + failed
    print(f"\n[nlu_corpus] 通过 {passed}/{total}（{passed / total * 100:.1f}%）")
    if failures:
        print(f"[nlu_corpus] 失败 {len(failures)} 条：")
        for f in failures:
            print(f)
    print("[nlu_corpus] 总体:", "PASS" if failed == 0 else "FAIL")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run())
