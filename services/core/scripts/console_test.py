#!/usr/bin/env python3
"""V2.1 控制台契约测试。

前置：mock-jarvis (:8080) 与 forkai-core (:19000) 已启动。
用法（services/core 目录下）：.venv/bin/python scripts/console_test.py
"""
import asyncio
import sys
import time
from pathlib import Path

import httpx

CORE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CORE_ROOT))
sys.path.insert(0, str(CORE_ROOT / "scripts"))
from _site_helper import clear_site_via_kiosk

BASE = "http://127.0.0.1:19000"
MOCK = "http://127.0.0.1:8080"
RESULTS: list = []

# 与 ConsoleController._DIRECTIONS 一致：(trans 符号, rot 符号)
_DIRECTIONS = [
    ("fwd", 1, 0),
    ("back", -1, 0),
    ("fwd_left", 1, 1),
    ("fwd_right", 1, -1),
    ("back_left", -1, -1),
    ("back_right", -1, 1),
    ("spin_left", 0, 1),
    ("spin_right", 0, -1),
]


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, cond))
    print(f"[CONSOLE] {'PASS' if cond else 'FAIL'}  {name}  {detail}", flush=True)


def _kiosk() -> str:
    return (CORE_ROOT / "data" / "console" / "kiosk.token").read_text(encoding="utf-8").strip()


async def pair_phone(c: httpx.AsyncClient) -> str:
    code = (await c.post(f"{BASE}/api/pair/start")).json()["code"]
    return (await c.post(f"{BASE}/api/pair/confirm", json={"code": code})).json()["pairToken"]


async def hold(c: httpx.AsyncClient, token: str) -> dict:
    await clear_site_via_kiosk(c)
    h = {"Authorization": f"Bearer {token}"}
    site = (await c.get(f"{BASE}/api/site", headers=h)).json()
    un = await c.post(f"{BASE}/api/site/unlock", headers=h, json={"code": site["code"]})
    if un.status_code != 200:
        raise RuntimeError(f"unlock failed {un.status_code} {un.text}")
    return h


def _sign(value: float) -> int:
    if abs(value) < 1e-6:
        return 0
    return 1 if value > 0 else -1


async def main() -> int:
    async with httpx.AsyncClient(timeout=30) as c:
        await c.post(f"{MOCK}/api/debug/state", json={"alarm": "normal", "motor": True, "omitForkDis": False, "fork_cur_dis": 0})

        # 1 自检无需配对
        sc = await c.get(f"{BASE}/api/console/selfcheck")
        body = sc.json()
        check("1.自检无需配对", sc.status_code == 200 and body.get("ok") is True and body.get("rosbridge", {}).get("detail") == "二期接入", str(body)[:180])

        # 没有车载屏配对时，另一台已配对终端可以抢占
        token_a = await pair_phone(c)
        ha = {"Authorization": f"Bearer {token_a}"}
        site_a = (await c.get(f"{BASE}/api/site", headers=ha)).json()
        held = await c.post(f"{BASE}/api/site/unlock", headers=ha, json={"code": site_a["code"]})
        token_b = await pair_phone(c)
        hb = {"Authorization": f"Bearer {token_b}"}
        site_b = (await c.get(f"{BASE}/api/site", headers=hb)).json()
        stolen_phone = await c.post(
            f"{BASE}/api/site/unlock", headers=hb, json={"code": site_b["code"], "force": True}
        )
        check(
            "1b.无车载屏时可抢占",
            held.status_code == 200 and stolen_phone.status_code == 200 and stolen_phone.json().get("succeed") is True,
            f"hold={held.status_code} steal={stolen_phone.status_code} {stolen_phone.text[:120]}",
        )

        # 2 车载屏口令
        bad = await c.post(f"{BASE}/api/pair/local", json={"kioskToken": "nope"})
        check("2.错误口令 403", bad.status_code == 403 and bad.json().get("error") == "invalid_kiosk", bad.text)
        good = await c.post(f"{BASE}/api/pair/local", json={"kioskToken": _kiosk()})
        gj = good.json()
        check("3.正确口令且 local", good.status_code == 200 and gj.get("local") is True and gj.get("pairToken"), good.text[:160])
        kiosk_h = {"Authorization": f"Bearer {gj['pairToken']}"}

        # 3 扫码凭证
        denied = await c.get(f"{BASE}/api/pair/join-ticket", headers={"Authorization": f"Bearer {await pair_phone(c)}"})
        check("4.手机不能签发凭证", denied.status_code == 403 and denied.json().get("error") == "not_local", denied.text)
        first = (await c.get(f"{BASE}/api/pair/join-ticket", headers=kiosk_h)).json()
        second = (await c.get(f"{BASE}/api/pair/join-ticket", headers=kiosk_h)).json()
        stale = await c.post(f"{BASE}/api/pair/join", json={"ticket": first["ticket"]})
        joined = await c.post(f"{BASE}/api/pair/join", json={"ticket": second["ticket"]})
        again = await c.post(f"{BASE}/api/pair/join", json={"ticket": second["ticket"]})
        jj = joined.json()
        site = (await c.get(f"{BASE}/api/site", headers={"Authorization": f"Bearer {jj.get('pairToken', '')}"})).json()
        holder_ok = site.get("session", {}) and site["session"].get("holderClientId") == jj.get("clientId")
        check(
            "5.凭证一次有效且扫码取得控制权",
            "join=" in second.get("url", "")
            and stale.status_code == 400
            and joined.status_code == 200
            and jj.get("local") is False
            and again.status_code == 400
            and holder_ok,
            f"stale={stale.status_code} join={joined.status_code} again={again.status_code} holder={holder_ok}",
        )

        # 4 强制门控
        phone = await pair_phone(c)
        ph = await hold(c, phone)
        other = await pair_phone(c)
        oh = {"Authorization": f"Bearer {other}"}
        site_o = (await c.get(f"{BASE}/api/site", headers=oh)).json()
        forced = await c.post(f"{BASE}/api/site/unlock", headers=oh, json={"code": site_o["code"], "force": True})
        check("6.手机 force 不能抢锁", forced.status_code == 409 and forced.json().get("error") == "held", forced.text)
        stolen = await c.post(f"{BASE}/api/site/unlock", headers=kiosk_h, json={"local": True, "force": True})
        check("7.车载屏可以强制收回", stolen.status_code == 200 and stolen.json().get("succeed") is True, stolen.text)

        # 5 通用接口拒绝 motor/safe
        ph = await hold(c, phone)
        motor = await c.post(f"{BASE}/api/control/motor", headers=ph, json={"flag": False})
        safe = await c.post(f"{BASE}/api/control/safe", headers=ph, json={"flag": False})
        check(
            "8.control motor/safe 拒绝",
            motor.status_code == 403 and motor.json().get("error") == "use_console" and safe.status_code == 403,
            f"motor={motor.status_code} safe={safe.status_code}",
        )

        # 6 八个方向
        dir_ok = True
        dir_detail = ""
        for name, t_sign, r_sign in _DIRECTIONS:
            r = await c.post(f"{BASE}/api/console/drive", headers=ph, json={"direction": name, "trans": 500, "start": True})
            if r.status_code != 200:
                dir_ok = False
                dir_detail = f"{name} {r.status_code} {r.text}"
                break
            body = r.json()
            st = (await c.get(f"{BASE}/api/state", headers=ph)).json()
            vel = [float(x) for x in str(st.get("vel", "0,0,0")).split(",")]
            if _sign(body.get("trans", 0)) != t_sign or _sign(body.get("rot", 0)) != r_sign:
                dir_ok = False
                dir_detail = f"{name} body={body}"
                break
            if _sign(vel[0]) != t_sign or _sign(vel[2]) != r_sign:
                dir_ok = False
                dir_detail = f"{name} vel={vel}"
                break
            await c.post(f"{BASE}/api/console/drive/stop", headers=ph)
        check("9.八方向符号", dir_ok, dir_detail)

        # 7 速度裁剪
        hi = await c.post(f"{BASE}/api/console/drive", headers=ph, json={"direction": "fwd", "trans": 5000, "start": True})
        lo = await c.post(f"{BASE}/api/console/drive", headers=ph, json={"direction": "fwd", "trans": 10, "start": False})
        await c.post(f"{BASE}/api/console/drive/stop", headers=ph)
        check(
            "10.速度裁剪",
            hi.status_code == 200 and hi.json().get("trans") == 1000 and lo.json().get("trans") == 100,
            f"hi={hi.json()} lo={lo.json()}",
        )

        # 8 关避障
        no_cf = await c.post(f"{BASE}/api/console/avoid", headers=ph, json={"enabled": False})
        safe_state = (await c.get(f"{BASE}/api/state", headers=ph)).json().get("safe")
        opened = await c.post(f"{BASE}/api/console/avoid", headers=ph, json={"enabled": False, "confirm": True})
        limited = await c.post(f"{BASE}/api/console/drive", headers=ph, json={"direction": "fwd", "trans": 800, "start": True})
        await c.post(f"{BASE}/api/console/drive/stop", headers=ph)
        restored = (await c.get(f"{BASE}/api/state", headers=ph)).json().get("safe")
        status = (await c.get(f"{BASE}/api/console/status", headers=ph)).json()
        check(
            "11.关避障需确认且限速，松手恢复",
            no_cf.status_code == 400
            and no_cf.json().get("error") == "confirm_required"
            and safe_state is True
            and opened.status_code == 200
            and limited.json().get("trans") == 300
            and restored is True
            and status.get("avoidEnabled") is True,
            f"no={no_cf.status_code} open={opened.status_code} lim={limited.text} safe_after={restored}",
        )
        await c.post(f"{BASE}/api/console/avoid", headers=ph, json={"enabled": False, "confirm": True})
        await asyncio.sleep(6.2)
        timed = (await c.get(f"{BASE}/api/console/status", headers=ph)).json()
        safe_timed = (await c.get(f"{BASE}/api/state", headers=ph)).json().get("safe")
        check("12.关避障 5 秒无操作自动恢复", timed.get("avoidEnabled") is True and safe_timed is True, f"status={timed.get('avoidEnabled')} safe={safe_timed}")

        # 9 货叉
        step = await c.post(f"{BASE}/api/console/fork", headers=ph, json={"axis": "lift", "mode": "step", "delta": 50})
        busy = await c.post(f"{BASE}/api/console/drive", headers=ph, json={"direction": "fwd", "trans": 200, "start": True})
        height_ok = False
        idle = False
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            info = (await c.get(f"{BASE}/api/state", headers=ph)).json().get("fork_info") or {}
            st = (await c.get(f"{BASE}/api/console/status", headers=ph)).json()
            if info.get("fork_height") == 170:
                height_ok = True
            if height_ok and st.get("forkBusy") is False:
                idle = True
                break
            await asyncio.sleep(0.2)
        target = await c.post(f"{BASE}/api/console/fork", headers=ph, json={"axis": "lift", "mode": "target", "pos": 120})
        await asyncio.sleep(2.5)
        moving = await c.post(f"{BASE}/api/console/drive", headers=ph, json={"direction": "fwd", "trans": 200, "start": True})
        blocked = await c.post(f"{BASE}/api/console/fork", headers=ph, json={"axis": "lift", "mode": "step", "delta": 50})
        await c.post(f"{BASE}/api/console/drive/stop", headers=ph)
        await c.post(f"{MOCK}/api/debug/state", json={"omitForkDis": True})
        unsupported = await c.post(f"{BASE}/api/console/fork", headers=ph, json={"axis": "extend", "mode": "step", "delta": 50})
        await c.post(f"{MOCK}/api/debug/state", json={"omitForkDis": False, "fork_cur_dis": 0})
        need = await c.post(f"{BASE}/api/console/fork", headers=ph, json={"axis": "extend", "mode": "target", "pos": 200})
        went = await c.post(f"{BASE}/api/console/fork", headers=ph, json={"axis": "extend", "mode": "target", "pos": 200, "confirm": True})
        reach_ok = False
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            info = (await c.get(f"{BASE}/api/state", headers=ph)).json().get("fork_info") or {}
            if info.get("fork_cur_dis") == 200:
                reach_ok = True
                break
            await asyncio.sleep(0.2)
        await c.post(f"{MOCK}/api/debug/state", json={"fork_cur_dis": 600})
        capped = await c.post(f"{BASE}/api/console/fork", headers=ph, json={"axis": "extend", "mode": "target", "pos": 9999, "confirm": True})
        cap_ok = False
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            info = (await c.get(f"{BASE}/api/state", headers=ph)).json().get("fork_info") or {}
            if info.get("fork_cur_dis") == 640:
                cap_ok = True
                break
            await asyncio.sleep(0.2)
        check(
            "13.货叉步进/目标/互斥/伸缩上限",
            step.status_code == 200
            and busy.status_code == 409
            and busy.json().get("error") == "fork_busy"
            and height_ok
            and idle
            and target.status_code == 200
            and moving.status_code == 200
            and blocked.status_code == 409
            and blocked.json().get("error") == "chassis_moving"
            and unsupported.status_code == 400
            and unsupported.json().get("error") == "not_supported"
            and need.status_code == 400
            and need.json().get("error") == "confirm_required"
            and went.status_code == 200
            and reach_ok
            and capped.status_code == 200
            and cap_ok,
            f"step={step.status_code} busy={busy.status_code}/{busy.json().get('error')} height={height_ok} idle={idle} "
            f"target={target.status_code} blocked={blocked.status_code}/{blocked.json().get('error')} "
            f"unsup={unsupported.status_code} need={need.status_code} reach={reach_ok} cap={cap_ok} capped={capped.text[:120]}",
        )

        # 10 参数
        view = (await c.get(f"{BASE}/api/console/params", headers=ph)).json()
        sections = view.get("sections") or {}
        front = ((sections.get("avoid") or {}).get("items") or {}).get("clearance_front_max") or {}
        nav = ((sections.get("nav") or {}).get("items") or {}).get("limit_trans_max_vel") or {}
        before_front = front.get("default")
        new_front = 2500 if before_front != 2500 else 2800
        no_confirm = await c.post(
            f"{BASE}/api/console/params",
            headers=ph,
            json={"changes": {"avoid": {"clearance_front_max": new_front}}},
        )
        bad_range = await c.post(
            f"{BASE}/api/console/params",
            headers=ph,
            json={"changes": {"avoid": {"clearance_front_max": 20000}}, "confirm": True},
        )
        saved = await c.post(
            f"{BASE}/api/console/params",
            headers=ph,
            json={"changes": {"avoid": {"clearance_front_max": new_front}}, "confirm": True},
        )
        after = (await c.get(f"{BASE}/api/console/params", headers=ph)).json()
        avoid_items = ((after.get("sections") or {}).get("avoid") or {}).get("items") or {}
        backups = (await c.get(f"{BASE}/api/console/params/backups", headers=ph)).json().get("backups") or []
        restored = await c.post(
            f"{BASE}/api/console/params/restore",
            headers=ph,
            json={"backupId": backups[0]["id"] if backups else "", "confirm": True},
        )
        back = (await c.get(f"{BASE}/api/console/params", headers=ph)).json()
        front_back = ((((back.get("sections") or {}).get("avoid") or {}).get("items") or {}).get("clearance_front_max") or {}).get("default")
        log = (await c.get(f"{BASE}/api/console/params/log", headers=ph)).json().get("log") or []
        idle_deadline = time.monotonic() + 8
        idle_state = {}
        while time.monotonic() < idle_deadline:
            idle_state = (await c.get(f"{BASE}/api/console/status", headers=ph)).json()
            if idle_state.get("forkBusy") is False and idle_state.get("chassisMoving") is False:
                break
            await asyncio.sleep(0.2)
        drv = await c.post(f"{BASE}/api/console/drive", headers=ph, json={"direction": "fwd", "trans": 200, "start": True})
        moved = (await c.get(f"{BASE}/api/state", headers=ph)).json()
        while_moving = await c.post(
            f"{BASE}/api/console/params",
            headers=ph,
            json={"changes": {"avoid": {"clearance_front_max": 2600}}, "confirm": True},
        )
        await c.post(f"{BASE}/api/console/drive/stop", headers=ph)
        check(
            "14.参数校验/整段/备份/日志/运动中拒绝",
            front.get("editable") is True
            and nav.get("editable") is False
            and no_confirm.status_code == 400
            and bad_range.status_code == 400
            and saved.status_code == 200
            and (avoid_items.get("clearance_front_max") or {}).get("default") == new_front
            and (avoid_items.get("clearance_front_min") or {}).get("default") == 50
            and bool(backups)
            and restored.status_code == 200
            and front_back == before_front
            and any(row.get("action") == "apply" for row in log)
            and while_moving.status_code == 409
            and while_moving.json().get("error") == "robot_moving",
            f"edit={front.get('editable')} nav={nav.get('editable')} save={saved.status_code} "
            f"before={before_front} now={(avoid_items.get('clearance_front_max') or {}).get('default')} "
            f"min={(avoid_items.get('clearance_front_min') or {}).get('default')} restore={front_back} "
            f"drive={drv.status_code}/{drv.text[:80]} vel={moved.get('vel')} mode={moved.get('mode')} "
            f"idle={idle_state.get('forkBusy')} moving={while_moving.status_code}/{while_moving.text[:120]}",
        )

        # 11 向导
        done = await c.post(f"{BASE}/api/console/setup/complete", headers=ph)
        got = (await c.get(f"{BASE}/api/console/setup", headers=ph)).json()
        reset_denied = await c.post(f"{BASE}/api/console/setup/reset")
        reset = await c.post(f"{BASE}/api/console/setup/reset", headers=ph)
        cleared = (await c.get(f"{BASE}/api/console/setup", headers=ph)).json()
        await c.post(f"{BASE}/api/console/setup/complete", headers=ph)
        check(
            "15.向导完成与重置",
            done.status_code == 200
            and got.get("completed") is True
            and reset_denied.status_code == 401
            and reset.status_code == 200
            and cleared.get("completed") is False,
            f"done={got} reset={reset.status_code} cleared={cleared}",
        )

        # 12 急停
        stranger = await pair_phone(c)
        estop = await c.post(f"{BASE}/api/console/estop", headers={"Authorization": f"Bearer {stranger}"})
        motor_off = False
        site_cleared = False
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            st = (await c.get(f"{BASE}/api/state", headers=ph)).json()
            motor_off = st.get("motor") is False and st.get("alarm") == "estop"
            sess = (await c.get(f"{BASE}/api/site", headers=ph)).json().get("session")
            site_cleared = not sess
            if motor_off and site_cleared:
                break
            await asyncio.sleep(0.4)
        ph = await hold(c, phone)
        no_rel = await c.post(f"{BASE}/api/console/estop/release", headers=ph, json={})
        rel = await c.post(f"{BASE}/api/console/estop/release", headers=ph, json={"confirm": True})
        final = (await c.get(f"{BASE}/api/console/status", headers=ph)).json()
        motor_on = (await c.get(f"{BASE}/api/state", headers=ph)).json().get("motor")
        check(
            "16.急停可被任意终端触发，解除需控制权和确认",
            estop.status_code == 200
            and motor_off
            and site_cleared
            and no_rel.status_code == 400
            and rel.status_code == 200
            and final.get("estopLatched") is False
            and motor_on is True,
            f"estop={estop.status_code} motor_off={motor_off} site={site_cleared} rel={no_rel.status_code}/{rel.status_code} latch={final.get('estopLatched')} motor={motor_on}",
        )

    fails = [n for n, ok in RESULTS if not ok]
    print(
        f"[CONSOLE] ===== 汇总: {len(RESULTS) - len(fails)}/{len(RESULTS)} PASS"
        + (f"，失败: {fails}" if fails else "，全部通过 ====="),
        flush=True,
    )
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
