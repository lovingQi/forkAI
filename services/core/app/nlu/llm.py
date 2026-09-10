"""LLMClient：云端 OpenAI 兼容接口做意图抽取。

规则未命中/复合指令时调用。按 cfg["llm"]["model"] 选择 SiliconFlow 或官方 DeepSeek。
temperature=0、enable_thinking=false；超时/缺 key/解析失败返回 None，降级纯规则。
"""
import json
import os
import re
import time

import httpx

from .prompts import SYSTEM_PROMPT

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

LLM_CATALOG = [
    {"id": "deepseek-flash", "name": "DeepSeek-flash（官方，默认）", "provider": "deepseek"},
    {"id": "deepseek-chat", "name": "DeepSeek-chat（官方）", "provider": "deepseek"},
    {"id": "deepseek-v4-pro", "name": "DeepSeek-v4-pro（官方）", "provider": "deepseek"},
    {
        "id": "deepseek-ai/DeepSeek-V3.2",
        "name": "DeepSeek-V3.2",
        "provider": "siliconflow",
    },
    {"id": "deepseek-ai/DeepSeek-V3", "name": "DeepSeek-V3", "provider": "siliconflow"},
    {"id": "Qwen/Qwen3.5-27B", "name": "Qwen3.5-27B", "provider": "siliconflow"},
    {"id": "Pro/zai-org/GLM-5.1", "name": "GLM-5.1", "provider": "siliconflow"},
]

LLM_CATALOG_IDS = {item["id"] for item in LLM_CATALOG}


def current_llm_model(cfg: dict) -> str:
    return str((cfg.get("llm") or {}).get("model") or "deepseek-flash")


def catalog_item(model_id: str) -> dict | None:
    for item in LLM_CATALOG:
        if item["id"] == model_id:
            return item
    return None


def resolve_llm(cfg: dict, model_id: str | None = None) -> tuple[str, str, str] | None:
    """返回 (base_url, api_key, model)；缺 key 或未知模型返回 None。"""
    llm_cfg = cfg.get("llm") or {}
    mid = model_id or current_llm_model(cfg)
    item = catalog_item(mid)
    if item is None:
        return None
    if item["provider"] == "deepseek":
        sub = llm_cfg.get("deepseek") or {}
        env_name = str(sub.get("api_key_env") or "FORKAI_DEEPSEEK_API_KEY")
        key = os.environ.get(env_name, "").strip()
        url = str(sub.get("base_url") or "https://api.deepseek.com/v1").rstrip("/")
        model = mid
    else:
        sub = llm_cfg.get("siliconflow") or {}
        env_name = str(sub.get("api_key_env") or "FORKAI_TTS_API_KEY")
        key = os.environ.get(env_name, "").strip()
        url = str(sub.get("base_url") or "https://api.siliconflow.cn/v1").rstrip("/")
        model = mid
    if not key or not url:
        return None
    return url, key, model


class LLMClient:
    def __init__(self, cfg: dict):
        llm_cfg = cfg.get("llm", {}) or {}
        self._cfg = cfg
        self._timeout = float(llm_cfg.get("timeout_s", 10))
        self.enabled = bool(llm_cfg.get("enabled", False))
        self._client = httpx.AsyncClient()
        self._warned_unreachable = False

    async def extract(self, text: str) -> list | None:
        """返回 [{"intent": str, "slots": dict}, ...]（≤3 项）；失败/不可用返回 None。"""
        if not self.enabled:
            return None
        content = await self.raw_content(text)
        if content is None:
            return None
        return self._parse(content)

    async def _chat(self, text: str, model_id: str | None = None) -> tuple[str | None, str | None]:
        ep = resolve_llm(self._cfg, model_id)
        if ep is None:
            self._warn_once("[forkai-core] llm 未配置 key，降级纯规则 NLU")
            return None, "未配置key"
        base_url, api_key, model = ep
        body: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "temperature": 0,
            "max_tokens": 256,
        }
        item = catalog_item(model_id or current_llm_model(self._cfg))
        if item and item["provider"] == "siliconflow":
            body["enable_thinking"] = False
        try:
            res = await self._client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=body,
                timeout=self._timeout,
            )
        except httpx.TimeoutException:
            self._warn_once("[forkai-core] llm 超时，降级纯规则 NLU")
            return None, "超时"
        except httpx.HTTPError as e:
            self._warn_once(f"[forkai-core] llm 不可达，降级纯规则 NLU: {e}")
            return None, "失败"
        if res.status_code == 401:
            self._warn_once("[forkai-core] llm 401，官方 key 无效")
            return None, "key无效"
        if not 200 <= res.status_code < 300:
            self._warn_once(f"[forkai-core] llm HTTP {res.status_code}")
            return None, f"HTTP{res.status_code}"
        try:
            content = res.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as e:
            self._warn_once(f"[forkai-core] llm 输出无法解析: {e}")
            return None, "失败"
        return content, None

    async def raw_content(self, text: str, model_id: str | None = None) -> str | None:
        content, _err = await self._chat(text, model_id)
        return content

    async def probe_model(self, model_id: str) -> dict:
        item = catalog_item(model_id) or {"id": model_id, "name": model_id}
        t0 = time.perf_counter()
        content, err = await self._chat("前进", model_id)
        if content is None:
            return {
                "id": model_id,
                "name": item["name"],
                "latencyMs": None,
                "error": err or "失败",
            }
        ms = int((time.perf_counter() - t0) * 1000)
        return {"id": model_id, "name": item["name"], "latencyMs": ms, "error": None}

    def _warn_once(self, msg: str) -> None:
        if not self._warned_unreachable:
            print(msg)
            self._warned_unreachable = True

    def reset_warn(self) -> None:
        self._warned_unreachable = False

    @staticmethod
    def _parse(content: str) -> list | None:
        text = (content or "").strip()
        m = _FENCE_RE.search(text)
        if m:
            text = m.group(1).strip()
        try:
            obj = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            print(f"[forkai-core] llm 输出非 JSON，丢弃: {text[:120]!r}")
            return None
        if not isinstance(obj, dict) or not isinstance(obj.get("intents"), list):
            return None
        out = []
        for item in obj["intents"]:
            if not isinstance(item, dict):
                continue
            intent = item.get("intent")
            slots = item.get("slots")
            if not isinstance(intent, str) or not intent:
                continue
            out.append({"intent": intent, "slots": slots if isinstance(slots, dict) else {}})
            if len(out) >= 3:
                break
        return out

    async def close(self) -> None:
        await self._client.aclose()
