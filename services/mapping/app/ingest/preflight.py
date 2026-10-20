"""开始前的 9 项检查。预检数据不进入地图。"""
import shutil
from pathlib import Path

from ..engine.cartographer_profile import load_runtime_profile, product_by_id
from ..errors import MappingError
from ..robot.urdf import transform
from .observe import observe_topics


def _check(checks: list, issues: list, check_id: str, ok: bool, label: str, detail: str = "") -> None:
    checks.append({"id": check_id, "status": "ready" if ok else "error", "label": label, "detail": detail})
    if not ok:
        issues.append({"code": "MAPPING_INPUT_INVALID", "message": detail or label})


def engine_blockers(cfg: dict) -> list[str]:
    eng = cfg["engines"]["cartographer"]
    reasons = []
    binary = Path(eng["bin"])
    if not binary.is_file() or not (binary.stat().st_mode & 0o111):
        reasons.append("引擎程序不存在或不可执行")
    proto = Path(eng["protoRoot"]) / "cartographer/cloud/proto/map_builder_service.proto"
    if not proto.is_file():
        reasons.append("缺少 map_builder_service.proto")
    lua = Path(eng["configDir"]) / eng["configBasename"]
    if not lua.is_file():
        reasons.append("缺少 Lua 配置")
    root = Path(eng["profileRoot"])
    if not (root / "1.json").is_file() and not (root / "1-odom.json").is_file():
        reasons.append("缺少运行参数")
    return reasons


async def run_preflight(draft: dict, cfg: dict, profiles: list[dict], model: dict, scheduler) -> dict:
    checks: list[dict] = []
    issues: list[dict] = []
    blockers = engine_blockers(cfg)
    _check(checks, issues, "engine", not blockers, "建图引擎文件", "；".join(blockers))
    free = shutil.disk_usage(cfg["dataDir"]).free
    _check(checks, issues, "disk", free >= int(cfg.get("minFreeBytes") or 0), "磁盘空间", f"剩余 {free} 字节")
    _check(checks, issues, "busy", scheduler.active_task_id is None, "没有其他建图任务", "已有任务在进行" if scheduler.active_task_id else "")
    product = None
    try:
        product = product_by_id(profiles, draft["profileId"])
        runtime = load_runtime_profile(Path(cfg["engines"]["cartographer"]["profileRoot"]), product["runtimeRevision"])
        _check(checks, issues, "profile", True, "建图参数")
    except MappingError as exc:
        runtime = None
        _check(checks, issues, "profile", False, "建图参数", exc.message)
    if product and runtime and not issues:
        specs = [{"topic": item["topic"], "kind": "range"} for item in draft["inputs"]["ranges"]]
        odom = draft["inputs"].get("odometry")
        if odom:
            specs.append({"topic": odom["topic"], "kind": "odometry"})
        tracking = (runtime.get("range") or {}).get("trackingFrame") or "base_link"
        try:
            observed = await observe_topics(
                cfg["foxglove"]["url"], specs, cfg["foxglove"]["preflightTimeoutMs"] / 1000, model, tracking,
            )
            _check(checks, issues, "bridge", True, "传感器话题")
            _check(checks, issues, "samples", True, "每路至少 3 条样本")
            _check(checks, issues, "time", True, "时间戳")
            frames_ok = True
            for item in draft["inputs"]["ranges"]:
                frame = observed[item["topic"]]["frame"]
                try:
                    transform(model, tracking, frame)
                except MappingError:
                    frames_ok = False
            _check(checks, issues, "frames", frames_ok, "激光外参", "" if frames_ok else "URDF 中找不到激光坐标系")
            odom_ok = True
            detail = ""
            if odom:
                child = observed[odom["topic"]]["child"]
                expected = (runtime.get("odometry") or {}).get("childFrame")
                if expected and child and child.lstrip("/") != str(expected).lstrip("/"):
                    odom_ok = False
                    detail = f"子坐标系是 {child}，参数要求 {expected}"
            _check(checks, issues, "odom", odom_ok, "里程计坐标系", detail)
        except MappingError as exc:
            _check(checks, issues, "bridge", False, "传感器话题", exc.message)
    status = "error" if issues else "ready"
    return {"status": status, "checks": checks, "issues": issues}
