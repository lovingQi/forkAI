"""yaml + 环境变量。FORKAI_MAPPING_CONFIG、FORKAI_MAPPING_PORT、FORKAI_MAPPING_DATA_DIR。"""
import os
from pathlib import Path

import yaml

SERVICE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = SERVICE_ROOT / "config" / "mapping.config.yaml"
_ENGINE_KEYS = ("bin", "protoRoot", "configDir", "profileRoot")


def _from_config(base: Path, raw: str) -> str:
    path = Path(str(raw))
    if path.is_absolute():
        return str(path)
    return str((base / path).resolve())


def _resolve_engines(cfg: dict, base: Path) -> None:
    engines = cfg.get("engines") or {}
    cartographer = engines.get("cartographer") or {}
    for key in _ENGINE_KEYS:
        if cartographer.get(key):
            cartographer[key] = _from_config(base, cartographer[key])
    traverse = engines.get("traverse") or {}
    if traverse.get("root"):
        traverse["root"] = _from_config(base, traverse["root"])


def load_config() -> dict:
    path = Path(os.environ.get("FORKAI_MAPPING_CONFIG", str(DEFAULT_CONFIG)))
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if os.environ.get("FORKAI_MAPPING_PORT"):
        cfg.setdefault("server", {})["port"] = int(os.environ["FORKAI_MAPPING_PORT"])
    data = os.environ.get("FORKAI_MAPPING_DATA_DIR") or cfg.get("dataDir") or "data"
    data_path = Path(data)
    if not data_path.is_absolute():
        data_path = (path.parent / data_path).resolve()
    cfg["dataDir"] = str(data_path)
    cfg["_configPath"] = str(path.resolve())
    _resolve_engines(cfg, path.parent)
    return cfg


def resolve_under(cfg: dict, raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return (Path(cfg["_configPath"]).parent / path).resolve()
