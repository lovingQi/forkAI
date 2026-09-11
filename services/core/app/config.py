"""配置加载：yaml + 环境变量覆盖（对齐 voice-gateway src/config.ts）。

环境变量优先级高于 yaml：
- FORKAI_CORE_CONFIG  指定配置文件路径（默认 config/core.config.yaml）
- JARVIS_BASE_URL     覆盖 jarvis.baseUrl
- FORKAI_PORT         覆盖 server.port
- VEHICLE_ID          覆盖 vehicleId
- FORKAI_TTS_API_KEY  云端 TTS/ASR/SiliconFlow LLM Key（不在此处覆盖 yaml）
- FORKAI_DEEPSEEK_API_KEY  官方 DeepSeek Key（不在此处覆盖 yaml）

界面所选 ASR/LLM 模型与 NLU 快路径开关写在 config/runtime_models.yaml，启动时合并进 cfg。
本地密钥写在 config/secrets.yaml（gitignore），仅当对应环境变量为空时注入。
"""
import os
from pathlib import Path

import yaml

CORE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = CORE_ROOT / "config" / "core.config.yaml"
RUNTIME_MODELS_PATH = CORE_ROOT / "config" / "runtime_models.yaml"
SECRETS_PATH = CORE_ROOT / "config" / "secrets.yaml"


def _apply_secrets() -> None:
    if not SECRETS_PATH.is_file():
        return
    with open(SECRETS_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        return
    for k, v in data.items():
        if not isinstance(k, str) or not k or v is None:
            continue
        if os.environ.get(k, "").strip():
            continue
        os.environ[k] = str(v).strip()


def load_config() -> dict:
    _apply_secrets()
    config_path = os.environ.get("FORKAI_CORE_CONFIG", str(DEFAULT_CONFIG_PATH))
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if os.environ.get("JARVIS_BASE_URL"):
        cfg["jarvis"]["baseUrl"] = os.environ["JARVIS_BASE_URL"]
    if os.environ.get("FORKAI_PORT"):
        cfg["server"]["port"] = int(os.environ["FORKAI_PORT"])
    if os.environ.get("VEHICLE_ID"):
        cfg["vehicleId"] = os.environ["VEHICLE_ID"]
    _merge_runtime_models(cfg)
    return cfg


def _merge_runtime_models(cfg: dict) -> None:
    if not RUNTIME_MODELS_PATH.is_file():
        return
    with open(RUNTIME_MODELS_PATH, "r", encoding="utf-8") as f:
        rt = yaml.safe_load(f) or {}
    asr_model = rt.get("asr_model")
    if asr_model:
        cfg.setdefault("asr", {}).setdefault("cloud", {})["model"] = str(asr_model)
    llm_model = rt.get("llm_model")
    if llm_model:
        cfg.setdefault("llm", {})["model"] = str(llm_model)
    nlu = cfg.setdefault("nlu", {})
    if "nlu_rules_enabled" in rt:
        nlu["rules_enabled"] = bool(rt["nlu_rules_enabled"])
    if "nlu_max_intents" in rt:
        try:
            nlu["max_intents"] = max(1, min(8, int(rt["nlu_max_intents"])))
        except (TypeError, ValueError):
            nlu["max_intents"] = 5
    if "llm_thinking_enabled" in rt:
        cfg.setdefault("llm", {})["thinking_enabled"] = bool(rt["llm_thinking_enabled"])


def save_runtime_models(cfg: dict) -> None:
    """原子写回所选模型与 NLU 快路径设置，供重启后记住。"""
    asr_model = str((cfg.get("asr") or {}).get("cloud", {}).get("model") or "")
    llm_model = str((cfg.get("llm") or {}).get("model") or "")
    nlu = cfg.get("nlu") or {}
    try:
        max_n = max(1, min(8, int(nlu.get("max_intents", 5))))
    except (TypeError, ValueError):
        max_n = 5
    data = {
        "asr_model": asr_model,
        "llm_model": llm_model,
        "nlu_rules_enabled": bool(nlu.get("rules_enabled", True)),
        "nlu_max_intents": max_n,
        "llm_thinking_enabled": bool((cfg.get("llm") or {}).get("thinking_enabled", False)),
    }
    RUNTIME_MODELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = RUNTIME_MODELS_PATH.with_suffix(".yaml.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    os.replace(tmp, RUNTIME_MODELS_PATH)


def now_ms() -> int:
    """与 JS Date.now() 对齐的毫秒时间戳。"""
    import time

    return int(time.time() * 1000)
