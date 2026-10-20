"""3D 开始前检查。雷达外参这一阶段不校验。"""
import json
import shutil
from pathlib import Path

from ..errors import MappingError
from .observe import observe_topics
from .preflight import _check


def traverse_blockers(cfg: dict) -> list[str]:
    raw = ((cfg.get("engines") or {}).get("traverse") or {}).get("root")
    if not raw:
        return ["未配置 Traverse 引擎"]
    root = Path(raw)
    reasons = []
    manifest_path = root / "runtime.json"
    if not manifest_path.is_file():
        return ["缺少 runtime.json"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ["runtime.json 无法解析"]
    executable = (root / str(manifest.get("executable") or "")).resolve()
    if not executable.is_file() or not (executable.stat().st_mode & 0o111):
        reasons.append("引擎程序不存在或不可执行")
    proto = (root / str(manifest.get("protoRoot") or "proto") / "traverse/engine/v1/traverse_engine.proto").resolve()
    if not proto.is_file():
        reasons.append("缺少 traverse_engine.proto")
    return reasons


async def run_preflight_3d(draft: dict, cfg: dict, profiles: list[dict], scheduler) -> dict:
    checks: list[dict] = []
    issues: list[dict] = []
    blockers = traverse_blockers(cfg)
    _check(checks, issues, "engine", not blockers, "建图引擎文件", "；".join(blockers))
    free = shutil.disk_usage(cfg["dataDir"]).free
    _check(checks, issues, "disk", free >= int(cfg.get("minFreeBytes") or 0), "磁盘空间", f"剩余 {free} 字节")
    _check(checks, issues, "busy", scheduler.active_task_id is None, "没有其他建图任务", "已有任务在进行" if scheduler.active_task_id else "")
    found = any(item.get("id") == draft["profileId"] and item.get("dimension") == "3d" for item in profiles)
    _check(checks, issues, "profile", found, "建图参数", "" if found else "建图参数不存在")
    if issues:
        return {"status": "error", "checks": checks, "issues": issues}
    lidar = draft["inputs"]["lidar"]["topic"]
    imu = draft["inputs"]["imu"]["topic"]
    try:
        observed = await observe_topics(
            cfg["foxglove"]["url"],
            [{"topic": lidar, "kind": "lidar"}, {"topic": imu, "kind": "imu"}],
            cfg["foxglove"]["preflightTimeoutMs"] / 1000,
            None,
            "base_link",
        )
        _check(checks, issues, "bridge", True, "传感器话题")
        _check(checks, issues, "samples", True, "每路至少 3 条样本")
        _check(checks, issues, "time", True, "时间戳")
        frame = str(observed[lidar].get("frame") or "")
        _check(checks, issues, "frames", bool(frame), "雷达坐标系", "外参未校验" if frame else "雷达坐标系为空")
    except MappingError as exc:
        _check(checks, issues, "bridge", False, "传感器话题", exc.message)
    return {"status": "error" if issues else "ready", "checks": checks, "issues": issues}
