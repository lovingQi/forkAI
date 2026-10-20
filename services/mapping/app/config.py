"""yaml + 环境变量。FORKAI_MAPPING_CONFIG、FORKAI_MAPPING_PORT。"""
import os
from pathlib import Path

import yaml

SERVICE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = SERVICE_ROOT / "config" / "mapping.config.yaml"


def load_config() -> dict:
    path = Path(os.environ.get("FORKAI_MAPPING_CONFIG", str(DEFAULT_CONFIG)))
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if os.environ.get("FORKAI_MAPPING_PORT"):
        cfg.setdefault("server", {})["port"] = int(os.environ["FORKAI_MAPPING_PORT"])
    data = cfg.get("dataDir") or "data"
    data_path = Path(data)
    if not data_path.is_absolute():
        data_path = (path.parent / data_path).resolve()
    cfg["dataDir"] = str(data_path)
    cfg["_configPath"] = str(path.resolve())
    return cfg


def resolve_under(cfg: dict, raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return (Path(cfg["_configPath"]).parent / path).resolve()
