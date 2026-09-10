"""配置加载：yaml + 环境变量覆盖（对齐 voice-gateway src/config.ts）。

环境变量优先级高于 yaml：
- FORKAI_CORE_CONFIG  指定配置文件路径（默认 config/core.config.yaml）
- JARVIS_BASE_URL     覆盖 jarvis.baseUrl
- FORKAI_PORT         覆盖 server.port
- VEHICLE_ID          覆盖 vehicleId
- FORKAI_TTS_API_KEY  云端 TTS Key（由 tts.cloud.api_key_env 指定，不在此处覆盖 yaml）
"""
import os
from pathlib import Path

import yaml

CORE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = CORE_ROOT / "config" / "core.config.yaml"


def load_config() -> dict:
    config_path = os.environ.get("FORKAI_CORE_CONFIG", str(DEFAULT_CONFIG_PATH))
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if os.environ.get("JARVIS_BASE_URL"):
        cfg["jarvis"]["baseUrl"] = os.environ["JARVIS_BASE_URL"]
    if os.environ.get("FORKAI_PORT"):
        cfg["server"]["port"] = int(os.environ["FORKAI_PORT"])
    if os.environ.get("VEHICLE_ID"):
        cfg["vehicleId"] = os.environ["VEHICLE_ID"]
    return cfg


def now_ms() -> int:
    """与 JS Date.now() 对齐的毫秒时间戳。"""
    import time

    return int(time.time() * 1000)
