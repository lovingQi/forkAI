#!/usr/bin/env python3
"""Week 2 出口检查（步骤34）：9 项一次跑通。

前置：mock-jarvis (:8080)、forkai-core (:19000)、llama-server (:19002) 已启动，
config 中 speak.beep=true、wakeArmMs=30000、llm.enabled=true。
脚本第 8 项会 pkill llama-server（测完不再恢复，跑完请自行重启）。

用法（services/core 目录下）：.venv/bin/python scripts/week2_regression.py
"""
import asyncio
import base64
import io
import json
import subprocess
import sys
import wave
from pathlib import Path

import httpx

CORE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CORE_ROOT))
WEB_DIR = CORE_ROOT.parent.parent / "apps" / "web"

BASE = "http://127.0.0.1:19000"
RESULTS: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, cond))
    print(f"[W2] {'PASS' if cond else 'FAIL'}  {name}  {detail}")


async def say(client: httpx.AsyncClient, token: str, text: str, channel: str = "ptt") -> dict:
    r = await client.post(
        f"{BASE}/api/voice/text",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": text, "channel": channel},
        timeout=30,
    )
    return r.json()


async def state(client: httpx.AsyncClient, token: str) -> dict:
    r = await client.get(f"{BASE}/api/state", headers={"Authorization": f"Bearer {token}"})
    return r.json()


def wav_info(b64: str):
    raw = base64.b64decode(b64)
    with wave.open(io.BytesIO(raw), "rb") as w:
        frames = w.readframes(w.getnframes())
        return w.getframerate(), w.getnframes() / w.getframerate(), frames


async def main() -> int:
    # 预检：llama-server 不在时第 4 项（复合指令走 LLM）必失败，先提示
    try:
        async with httpx.AsyncClient(timeout=3) as probe:
            await probe.get("http://127.0.0.1:19002/health")
        print("[W2] 预检: llama-server 在线")
    except Exception:
        print("[W2] 预检: llama-server 不可达！第4项需要它，请先启动 run-llama-server.sh")
        return 2
    async with httpx.AsyncClient(timeout=30) as c:
        # ---- 1. 配对 + 现场解锁（已有持锁者时 force 接管，core 跨轮次内存持锁） ----
        code = (await c.post(f"{BASE}/api/pair/start")).json()["code"]
        conf = (await c.post(f"{BASE}/api/pair/confirm", json={"code": code})).json()
        token = conf["pairToken"]
        h = {"Authorization": f"Bearer {token}"}
        site = (await c.get(f"{BASE}/api/site", headers=h)).json()
        un = await c.post(f"{BASE}/api/site/unlock", headers=h, json={"code": site["code"]})
        if un.status_code == 409:
            un = await c.post(
                f"{BASE}/api/site/unlock", headers=h,
                json={"code": site["code"], "force": True},
            )
        check("1.配对+现场解锁", un.status_code == 200 and un.json().get("succeed") is True)

        # ---- 2. 六任务语音触发 ----
        pose0 = (await state(c, token))["pose"]
        r = await say(c, token, "前进")
        ok1 = r["succeed"] and r["intent"]["name"] == "MOVE_FWD"
        r = await say(c, token, "原地转90度")
        ok2 = r["succeed"] and r["intent"]["name"] == "TASK_HEAD"
        await asyncio.sleep(1.5)
        pose1 = (await state(c, token))["pose"]
        ok2 = ok2 and pose1 != pose0
        r = await say(c, token, "升到150毫米")
        ok3 = r["succeed"] and r["intent"]["name"] == "FORK_LIFT_TO"
        r = await say(c, token, "从A点到B点")
        ok4 = r["succeed"] and r["utterance"].startswith("确认执行：")
        r = await say(c, token, "确认")
        ok4 = ok4 and r["succeed"] and r["utterance"].startswith("好的，从")
        r = await say(c, token, "识别栈板")
        ok5 = r["succeed"] and r["intent"]["name"] == "TASK_GET_PALLET"
        r = await say(c, token, "去1号充电桩充电")
        ok6 = r["succeed"] and r["intent"]["name"] == "TASK_CHARGE"
        await asyncio.sleep(2.5)
        st = await state(c, token)
        ok3 = ok3 and st["fork_info"]["fork_height"] == 150
        ok6 = ok6 and st["charing"] is True
        check("2.六任务触发", all([ok1, ok2, ok3, ok4, ok5, ok6]),
              f"点动={ok1} head={ok2} fork150={ok3} fb确认={ok4} pallet={ok5} charge={ok6}")
        await say(c, token, "停止")  # 复位 charing

        # ---- 3. 参数追问 ----
        r1 = await say(c, token, "盲叉取货")
        r2 = await say(c, token, "A点")
        r3 = await say(c, token, "B点")
        r4 = await say(c, token, "确认")
        ok = (
            r1["utterance"] == "请告诉我起点"
            and r2["utterance"] == "请告诉我终点"
            and r3["utterance"].startswith("确认执行：")
            and r4["succeed"] and r4["utterance"].startswith("好的，从")
        )
        check("3.参数追问(盲叉取货)", ok,
              f"{r1['utterance']!r}→{r2['utterance']!r}→{r3['utterance']!r}→{r4['utterance']!r}")

        # ---- 4. 复合指令 ----
        r = await say(c, token, "升到150毫米然后去A区")
        names = [i["name"] for i in r.get("intents", [])]
        check("4.复合指令双意图", r["succeed"] and names == ["FORK_LIFT_TO", "GOTO_GOAL"],
              f"intents={names} last={r['utterance']!r}")

        # ---- 5. 问答三连 ----
        r1 = await say(c, token, "速度多少")
        r2 = await say(c, token, "当前任务")
        r3 = await say(c, token, "为什么停")
        # 新 mock（真车语义）：/api/state 无 speed 字段 → vel 兜底 m/s 话术（停车后 vel=0）；
        # route 结束后 routes="TEMP_DEFAULT" → "当前没有任务"
        ok = (
            r1["utterance"] == "当前速度0.0米每秒"
            and r2["utterance"] == "当前没有任务"
            and r3["utterance"] == "当前没有告警，车辆正常"
        )
        check("5.问答三连", ok, f"{r1['utterance']!r} / {r2['utterance']!r} / {r3['utterance']!r}")

        # ---- 6. 提示音（前导静音垫≈250ms + beep 前缀样本级一致 + 时长差） ----
        r = await say(c, token, "前进")
        with wave.open(str(CORE_ROOT / "assets" / "beep_ok.wav"), "rb") as w:
            beep_frames = w.readframes(w.getnframes())
            beep_dur = w.getnframes() / w.getframerate()
        rate, dur, frames = wav_info(r["audioBase64"])
        pad = int(rate * 0.25) * 2  # leadSilenceMs=250，16bit 单声道
        pad_ok = frames[:pad] == b"\x00" * pad
        prefix_ok = frames[pad : pad + len(beep_frames)] == beep_frames
        dur_ok = dur > 0.25 + beep_dur + 0.3
        check("6.提示音拼接", pad_ok and prefix_ok and dur_ok,
              f"静音垫:{pad_ok} 前缀==beep_ok:{prefix_ok} 总时长={dur:.2f}s(beep {beep_dur:.2f}s)")

        # ---- 7. 连续对话 30s 免唤醒（滚动续期：t=0/25/50 三次 cabin 指令） ----
        r = await say(c, token, "玖物玖物", channel="cabin")
        ok = r["succeed"] and r["intent"]["name"] == "WAKE"
        r = await say(c, token, "前进", channel="cabin")
        ok = ok and r["succeed"] and r["intent"]["name"] == "MOVE_FWD"
        await asyncio.sleep(25)
        r = await say(c, token, "后退", channel="cabin")
        ok25 = r["succeed"] and r["intent"]["name"] == "MOVE_BACK"
        await asyncio.sleep(25)
        r = await say(c, token, "前进", channel="cabin")
        ok50 = r["succeed"] and r["intent"]["name"] == "MOVE_FWD"
        # t=50s 已超初始 30s 窗口，仍成功 → 滚动续期生效
        check("7.连续对话滚动免唤醒", ok and ok25 and ok50,
              f"wake={ok} t25={ok25} t50={ok50}")
        await say(c, token, "停止")

        # ---- 8. 降级：kill llama-server ----
        subprocess.run(["pkill", "-x", "llama-server"], check=False)
        await asyncio.sleep(1)
        r1 = await say(c, token, "前进")
        r2 = await say(c, token, "升到2米然后去A区")
        names = [i["name"] for i in r2.get("intents", [])]
        ok = r1["succeed"] and r2["succeed"] and names == ["FORK_LIFT_TO"]
        check("8.LLM降级", ok, f"前进succeed={r1['succeed']} 复合降级intents={names}")
        await say(c, token, "停止")

        # ---- 9. 前端 typecheck + build ----
        tsc = subprocess.run(
            ["npx", "vue-tsc", "-b", "--force"], cwd=str(WEB_DIR),
            capture_output=True, timeout=300,
        )
        build = subprocess.run(
            ["npx", "vite", "build"], cwd=str(WEB_DIR),
            capture_output=True, timeout=300,
        )
        check("9.前端typecheck+build", tsc.returncode == 0 and build.returncode == 0,
              f"vue-tsc={tsc.returncode} vite={build.returncode}")

    fails = [n for n, ok in RESULTS if not ok]
    print(f"[W2] ===== 汇总: {len(RESULTS) - len(fails)}/{len(RESULTS)} PASS"
          + (f"，失败: {fails}" if fails else "，全部通过 ====="))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
