"""FlowEngine：任务流执行引擎（步骤37-39）。

- app.state 单例；全局同一时刻只允许一条流 running|paused（start 冲突抛 FlowBusyError）
- 状态机：idle/running/paused/succeeded/failed/cancelled
- 节点状态：pending|running|paused|succeeded|failed|skipped
  （"paused" 为节点态扩展：暂停时当前节点记为 paused，resume 从头重发该节点）
- 并行组：组内节点并发执行，全部 succeeded 才出组；任一 failed → 该成员的 fail 边
  （组级别 adj 取成员第一条 fail 边），无则流 failed
- 完成判定集中在 _is_node_done（mock 语义；真车语义待步骤21校准，只改这一个函数）
- 低电量：节点轮询时检查 state.battery < low_battery_pct → 自动 pause +
  TTS flow_low_battery + 自动下发 charge(goal=auto)；充到 resume_battery_pct 广播
  flow_charge_full（默认不自动 resume，人工/API 恢复——避免无人值守车在充电桩前自启动）
- 每次节点/流状态变化 broadcast "flow_event" {flowId,nodeId,nodeStatus,flowStatus}
"""
import asyncio
import math

from ..jarvis.routes_builder import build_route
from ..jarvis.station_names import resolve_station_fields
from ..speak import render
from ..tts.service import synthesize
from .schema import build_units, _group_map


def _pose_th(state: dict) -> float | None:
    """从 state.pose（"x,y,th" 字符串）取朝向角 th。"""
    try:
        return float(str(state.get("pose") or "0,0,0").split(",")[2])
    except (ValueError, IndexError):
        return None


def _norm_angle(a: float) -> float:
    """归一化到 (-pi, pi]。"""
    while a > math.pi:
        a -= 2 * math.pi
    while a <= -math.pi:
        a += 2 * math.pi
    return a


class FlowBusyError(Exception):
    pass


class _Paused(Exception):
    """节点执行中检测到暂停请求（resume 后由 _run_node 从头重发）。"""


class _Redispatch(Exception):
    """jarvis 断连恢复后重发当前节点（与 resume 同语义从头执行）。

    真实 jarvis 掉线/重启后任务状态不可信，继续盲等完成标记可能永远等不到；
    重发是当前最安全的恢复语义（各 route 任务幂等可重入）。"""


class FlowEngine:
    def __init__(self, cfg: dict, store, jarvis, bus):
        self._cfg = cfg
        self._store = store
        self._jarvis = jarvis
        self._bus = bus
        tf = cfg.get("taskflow", {}) or {}
        self._node_timeout_s = float(tf.get("node_timeout_s", 120))
        self._low_battery_pct = float(tf.get("low_battery_pct", 20))
        self._resume_battery_pct = float(tf.get("resume_battery_pct", 80))
        self._poll_s = float(tf.get("poll_ms", 500)) / 1000.0

        self._status = "idle"
        self._flow = None
        self._node_states: dict = {}
        self._current_unit = None  # 当前单元 key（节点 id 或 g{i}）
        self._current_node_id = None
        self._task: asyncio.Task | None = None
        self._pause_flag = False
        self._pause_gen = 0  # pause 代数：每次 pause 递增，用于检测"执行期间被打断"
        self._resume_event = asyncio.Event()
        self._shutting_down = False

    # ---- 对外状态 ----
    def is_busy(self) -> bool:
        return self._status in ("running", "paused")

    def status(self) -> dict:
        return {
            "flowId": (self._flow or {}).get("id"),
            "flowStatus": self._status,
            "currentNodeId": self._current_node_id,
            "nodeStates": dict(self._node_states),
        }

    def _emit(self, node_id=None, node_status=None) -> None:
        self._bus.broadcast(
            "flow_event",
            {
                "flowId": (self._flow or {}).get("id"),
                "nodeId": node_id,
                "nodeStatus": node_status,
                "flowStatus": self._status,
            },
        )

    def _set_flow_status(self, status: str) -> None:
        self._status = status
        self._emit()
        if status in ("succeeded", "failed", "cancelled"):
            self._drop_snapshot()
        else:
            self._save_snapshot()

    def _set_node_state(self, nid: str, state: str) -> None:
        self._node_states[nid] = state
        self._emit(nid, state)
        self._save_snapshot()

    # ---- 状态快照（步骤49：崩溃恢复） ----
    @property
    def _snapshot_path(self):
        return self._store.dir / ".engine_state.json"

    def _save_snapshot(self) -> None:
        """running/paused 状态迁移时落盘；供 core 重启后恢复为 paused。"""
        if self._status not in ("running", "paused") or not self._flow:
            return
        import json

        try:
            self._snapshot_path.write_text(
                json.dumps(
                    {
                        "flowId": self._flow.get("id"),
                        "currentNodeId": self._current_node_id,
                        "currentUnit": self._current_unit,
                        "nodeStates": self._node_states,
                        "flowStatus": self._status,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _drop_snapshot(self) -> None:
        try:
            self._snapshot_path.unlink()
        except OSError:
            pass

    async def restore_snapshot(self) -> bool:
        """core 启动时调用：存在 running/paused 快照 → 恢复为 paused（安全）+ 广播 + 日志。

        恢复后流处于 paused，任务重新挂起等待人工 resume（resume 语义=当前单元
        从头重发，与既有 pause/resume 一致）；流已删除则丢弃快照。
        """
        import json

        try:
            snap = json.loads(self._snapshot_path.read_text(encoding="utf-8"))
        except Exception:
            return False
        if snap.get("flowStatus") not in ("running", "paused"):
            return False
        flow = await self._store.get(snap.get("flowId") or "")
        if not flow:
            self._drop_snapshot()
            return False
        self._flow = flow
        self._node_states = snap.get("nodeStates") or {
            n["id"]: "pending" for n in flow["nodes"]
        }
        self._current_unit = snap.get("currentUnit")
        self._current_node_id = snap.get("currentNodeId")
        self._pause_flag = True
        self._resume_event.clear()
        self._status = "paused"
        print(f"[forkai-core] 恢复任务流快照 flow={flow.get('id')} → paused（人工 resume 继续）")
        self._emit()
        # 重建执行任务：挂起在暂停门内，等人工 resume 后从当前单元重发
        self._task = asyncio.create_task(self._run(start_unit=self._current_unit))
        return True

    # ---- 控制 ----
    async def start(self, flow_id: str) -> None:
        if self.is_busy():
            raise FlowBusyError("already_running")
        flow = await self._store.get(flow_id)
        if not flow:
            raise KeyError(flow_id)
        self._flow = flow
        self._node_states = {n["id"]: "pending" for n in flow["nodes"]}
        self._current_unit = None
        self._current_node_id = None
        self._pause_flag = False
        self._resume_event.clear()
        self._set_flow_status("running")
        self._task = asyncio.create_task(self._run())

    async def pause(self) -> bool:
        if self._status != "running":
            return False
        self._pause_flag = True
        self._pause_gen += 1  # 标记一次打断，_run_node 据此重发
        # 立即停车（步骤38）
        try:
            await self._jarvis.control("stop")
        except Exception:
            pass
        self._status = "paused"
        if self._current_node_id:
            self._set_node_state(self._current_node_id, "paused")
        self._emit()
        return True

    async def resume(self) -> bool:
        if self._status != "paused":
            return False
        self._pause_flag = False
        self._status = "running"
        # 先清再 set：防止 _wait_paused 在 resume 后迟到执行 clear() 吞掉信号
        self._resume_event.clear()
        self._resume_event.set()
        self._emit()
        return True

    async def cancel(self) -> bool:
        if not self.is_busy():
            return False
        try:
            await self._jarvis.control("stop")
        except Exception:
            pass
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        return True

    async def shutdown(self) -> None:
        # 进程退出 ≠ 用户取消：保留 running/paused 快照供下次启动恢复
        self._shutting_down = True
        await self.cancel()

    # ---- 主循环 ----
    async def _run(self, start_unit: str | None = None) -> None:
        flow = self._flow
        _errs: list = []
        member_of = _group_map(flow.get("parallel_groups") or [],
                               {n["id"] for n in flow["nodes"]}, _errs)
        _units, adj, members, entries = build_units(
            [n["id"] for n in flow["nodes"]], flow.get("edges") or [], member_of
        )
        node_by_id = {n["id"]: n for n in flow["nodes"]}
        # 流级 options 覆盖 config（验证/调试便利；生产用 config taskflow 段）
        opts = flow.get("options") or {}
        if opts.get("node_timeout_s") is not None:
            self._node_timeout_s = float(opts["node_timeout_s"])
        else:
            self._node_timeout_s = float(
                (self._cfg.get("taskflow") or {}).get("node_timeout_s", 120)
            )

        unit = start_unit if start_unit in _units else (sorted(entries)[0] if entries else None)
        final = "succeeded"
        try:
            while unit:
                self._current_unit = unit
                outcome = await self._run_unit(unit, members[unit], node_by_id)
                if outcome == "succeeded":
                    nxt = adj[unit]["success"]
                    if nxt is None:
                        final = "succeeded"
                        break
                else:
                    nxt = adj[unit]["fail"]
                    if nxt is None:
                        final = "failed"
                        break
                unit = nxt
            self._set_flow_status(final)
        except asyncio.CancelledError:
            if self._shutting_down:
                # 进程退出：不标 cancelled、保留快照（状态停留 running/paused）
                self._save_snapshot()
                raise
            for n in flow["nodes"]:
                if self._node_states.get(n["id"]) in ("pending", "running", "paused"):
                    self._node_states[n["id"]] = "skipped"
            self._set_flow_status("cancelled")  # 终态：清快照
            raise
        except Exception as e:
            print(f"[forkai-core] flow engine 异常: {e}")
            self._set_flow_status("failed")
        finally:
            self._current_unit = None
            self._current_node_id = None

    async def _run_unit(self, unit: str, node_ids: list, node_by_id: dict) -> str:
        """单节点或并行组；返回 succeeded|failed。"""
        if len(node_ids) == 1:
            return await self._run_node(node_by_id[node_ids[0]])
        # 并行组：并发执行，全部 succeeded 才出组
        results = await asyncio.gather(
            *(self._run_node(node_by_id[nid]) for nid in node_ids)
        )
        return "succeeded" if all(r == "succeeded" for r in results) else "failed"

    async def _run_node(self, node: dict) -> str:
        """执行单节点；pause 后 resume 从头重发 route（步骤38）。"""
        nid = node["id"]
        while True:
            if self._pause_flag:
                await self._wait_paused()
            self._current_node_id = nid
            self._set_node_state(nid, "running")
            # 记录本次执行的起始 pause 代数：执行期间若发生过 pause（代数变化），
            # 即使 _execute_node_once 正常返回也必须重发（车端 route 已被 stop 中止）
            pause_gen = self._pause_gen
            try:
                outcome = await self._execute_node_once(node)
            except _Paused:
                continue  # resume 后重发
            except _Redispatch:
                continue  # jarvis 断连恢复后重发
            if self._pause_gen != pause_gen:
                continue  # 执行期间被 pause 打断过，重发
            self._set_node_state(nid, "succeeded" if outcome else "failed")
            return "succeeded" if outcome else "failed"

    async def _wait_paused(self) -> None:
        """挂起等待 resume / cancel（cancel 经 task.cancel 注入 CancelledError）。"""
        # 不在此处 clear：resume() 已 set 的信号必须能被本轮 wait 看到；
        # 每次进入挂起前由 resume() 负责 clear+set 的配对，避免竞态吞信号。
        while self._pause_flag:
            try:
                await asyncio.wait_for(self._resume_event.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass
            # 超时后重查 _pause_flag；resume 已将其置 False 则退出
            if not self._pause_flag:
                break

    async def _execute_node_once(self, node: dict) -> bool:
        ntype, params = node["type"], dict(node.get("params") or {})
        if ntype == "drive":
            return await self._exec_drive(params)
        if ntype == "follow_back":
            params = resolve_station_fields(params, await self._jarvis.path_point_names())
        route = build_route(ntype, params)
        route_name = route["name"]
        # 节点下发重试 3 次（间隔 1s）仍失败才判 failed（步骤49 断网续跑）
        dispatched = False
        for attempt in range(3):
            try:
                await self._jarvis.start_route(route)
                dispatched = True
                break
            except Exception as e:
                print(f"[forkai-core] 节点 {node['id']} 下发失败(第{attempt + 1}次): {e}")
                if self._pause_flag:
                    raise _Paused()
                await asyncio.sleep(1)
        if not dispatched:
            return False
        # start_route 的 await 期间可能发生了 pause（HTTP 请求不检查 flag）：
        # 若 pause 已生效（车端已 stop、route 被中止），抛 _Paused 让 _run_node 重发
        if self._pause_flag:
            raise _Paused()
        # 节点级 timeout_s 覆盖（测试/特殊节点），否则流 options / config
        timeout_s = float(params.get("timeout_s") or self._node_timeout_s)
        deadline = asyncio.get_running_loop().time() + timeout_s
        # 启动宽限：jarvis Start 为异步入队，route 名出现在 state 前需要一小段时间
        start_grace_deadline = asyncio.get_running_loop().time() + min(10.0, timeout_s)
        # head 判定基准：下发时刻的朝向
        ctx = {"seen_running": False, "start_th": None, "grace_deadline": start_grace_deadline}
        try:
            st0 = await self._jarvis.get_state()
            ctx["start_th"] = _pose_th(st0)
        except Exception:
            pass
        poll_failures = 0
        lost_announced = False
        last_lost_broadcast = 0.0
        while True:
            if self._pause_flag:
                raise _Paused()
            await asyncio.sleep(self._poll_s)
            if self._pause_flag:
                raise _Paused()
            try:
                state = await self._jarvis.get_state()
                poll_failures = 0
                if lost_announced:
                    # 恢复：广播一次，重发当前节点（见 _Redispatch 注释），继续轮询
                    self._bus.broadcast(
                        "flow_jarvis_back", {"flowId": (self._flow or {}).get("id")}
                    )
                    raise _Redispatch()
            except _Redispatch:
                raise
            except Exception:
                poll_failures += 1
                now = asyncio.get_running_loop().time()
                if now - last_lost_broadcast >= 2.0:
                    self._bus.broadcast(
                        "flow_jarvis_lost",
                        {"flowId": (self._flow or {}).get("id"), "failures": poll_failures},
                    )
                    last_lost_broadcast = now
                    lost_announced = True
                if poll_failures >= 10:
                    # 连续失败 ≈5s 才判节点 failed
                    return False
                continue
            # 低电量检查（步骤39）：先于完成判定
            battery = state.get("battery")
            if isinstance(battery, (int, float)) and battery < self._low_battery_pct:
                await self._low_battery_pause()
                raise _Paused()
            verdict = self._judge_node(state, node, route_name, ctx)
            if verdict is not None:
                return verdict
            now = asyncio.get_running_loop().time()
            if not ctx["seen_running"] and now > start_grace_deadline:
                # 超过启动宽限仍未见 route 运行 → 判 failed（如下发被吞/参数非法）
                return False
            if now > deadline:
                # 节点超时 → failed（停车防止裸奔）
                try:
                    await self._jarvis.control("stop")
                except Exception:
                    pass
                return False

    def _judge_node(self, state: dict, node: dict, route_name: str, ctx: dict):
        """节点完成/失败判定（唯一集中点）。返回 True=succeeded / False=failed / None=进行中。

        源码依据（jarvis-fork）：
        - route 结束（成功/失败/中止相同表现）：LoopOnce 调 SetDefaultRICK →
          current_routes.routes 变为 "TEMP_DEFAULT"、key="a"、mode 回默认 "Idle"
          （libgrm.x86.a 反汇编：SetDefaultRICK 常量 TEMP_DEFAULT/a/{cmd:idle}；
          JModeIdle 名 "Idle"，ext/grm_ext/src/task/JModeIdle.cpp）
        - /api/state 不暴露成功/失败：JRouteInfo.state（SUCCESS=2/FAIL=3，JRouteInfo.h:35）
          未被映射（JWebService.cpp:166-172，status 硬编码 "running"），
          GetRouteChangeRecord 无 web 暴露 → 只能按任务类型物理量复合判定
        - 瞬时完成的 route（如叉高本已在目标）可能在两次轮询间就结束，
          从未被观察到 running —— 此时只要物理量已满足即判成功
        """
        cr = state.get("current_routes") or {}
        cur = cr.get("routes") if isinstance(cr, dict) else None
        if cur == route_name:
            ctx["seen_running"] = True
            return None
        if ctx.get("seen_running"):
            # route 名消失（→ TEMP_DEFAULT 或被顶替）= 任务已结束，按类型判成败
            v = self._physical_verdict(node, state, ctx)
            return True if v is None else v
        # 从未观察到运行：异步入队中，或瞬时完成
        if cur in ("", "TEMP_DEFAULT"):
            v = self._physical_verdict(node, state, ctx)
            if v is True:
                return True  # 瞬时完成（物理量已满足）
            return None  # 继续等待（启动宽限/节点超时由调用方兜底）
        # cur 是其他 route 名：可能是上一个 route 的残留（异步入队尚未切换），
        # 也可能是被顶替。启动宽限内且物理量未达标时继续等待，宽限外才判顶替失败。
        now = asyncio.get_running_loop().time()
        if now < ctx.get("grace_deadline", 0):
            return None
        return False  # 被其他 route 顶替

    def _physical_verdict(self, node: dict, state: dict, ctx: dict):
        """按任务类型的物理量判定。True=达标 / False=未达标 / None=无法判定。"""
        ntype = node["type"]
        params = node.get("params") or {}
        if ntype == "focklift":
            target = int(params.get("pos", 0))
            tol = max(int(params.get("tolerance", 20)), 10)
            h = (state.get("fork_info") or {}).get("fork_height")
            if not isinstance(h, (int, float)):
                return False
            return abs(h - target) <= tol
        if ntype == "charge":
            return state.get("charing") is True
        if ntype == "head":
            th_now = _pose_th(state)
            if ctx.get("start_th") is None or th_now is None:
                return None
            want = math.radians(float(params.get("angle", 0)))
            delta = _norm_angle(th_now - ctx["start_th"])
            return abs(delta - want) <= 0.15  # ≈8.6° 容差
        # follow_back / get_pallet：state 无物理量可判（目标点是地图名、识别结果无字段）
        return None

    async def _exec_drive(self, params: dict) -> bool:
        """流程内定时点动：直接 control('drive') → 定时 → control('stop')，不走 watchdog。
        下发重试 3 次（步骤49 断网续跑）。"""
        dispatched = False
        for attempt in range(3):
            try:
                await self._jarvis.control(
                    "drive",
                    {
                        "trans": params.get("trans", 0),
                        "rot": params.get("rot", 0),
                        "speed": params.get("speed", 20),
                    },
                )
                dispatched = True
                break
            except Exception:
                if self._pause_flag:
                    raise _Paused()
                await asyncio.sleep(1)
        if not dispatched:
            return False
        duration = float(params.get("duration_s", 1))
        elapsed = 0.0
        while elapsed < duration:
            if self._pause_flag:
                raise _Paused()
            await asyncio.sleep(min(0.2, duration - elapsed))
            elapsed += 0.2
        try:
            await self._jarvis.control("stop")
        except Exception:
            pass
        return True

    async def _low_battery_pause(self) -> None:
        """低电量自动暂停 + TTS 播报 + 自动下发充电路线；充到阈值广播 flow_charge_full。"""
        self._pause_flag = True
        try:
            await self._jarvis.control("stop")
        except Exception:
            pass
        self._status = "paused"
        if self._current_node_id:
            self._set_node_state(self._current_node_id, "paused")
        self._emit()
        # TTS 广播话术（复用 piper 合成，前端走既有 tts 事件播放）
        text = render("flow_low_battery")
        spoken = {}
        try:
            spoken = await synthesize(text, "fail", self._cfg)
            audio = spoken.get("audio_base64")
        except Exception:
            audio = None
        self._bus.broadcast(
            "tts",
            {
                "text": text,
                "style": "fail",
                "target": "both",
                "audioBase64": audio,
                "ttsEngine": spoken.get("engine"),
                "clientId": None,
            },
        )
        # 自动下发充电路线（独立于流，直接调度）
        try:
            await self._jarvis.start_route(build_route("charge", {"goal": "auto"}))
        except Exception as e:
            print(f"[forkai-core] 低电量自动充电路线下发失败: {e}")
        # 等待充满（不自动 resume：人工/API 恢复，避免无人值守自启动）
        while self._pause_flag:
            await asyncio.sleep(self._poll_s * 4)
            try:
                state = await self._jarvis.get_state()
            except Exception:
                continue
            battery = state.get("battery")
            if isinstance(battery, (int, float)) and battery >= self._resume_battery_pct:
                self._bus.broadcast(
                    "flow_charge_full",
                    {"flowId": (self._flow or {}).get("id"), "battery": battery},
                )
                return
