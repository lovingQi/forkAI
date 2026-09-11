"""LLMClient：云端 OpenAI 兼容接口做意图抽取。

规则快路径未命中或关闭时调用。按 cfg["llm"]["model"] 选择 SiliconFlow 或官方 DeepSeek。
思考模式可关：关则把额度留给 JSON；开则加大 max_tokens，只解析 message.content。
超时/缺 key/解析失败返回 None（整句不执行）。
"""
import json
import os
import re
import time

import httpx

from .prompts import system_prompt

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

    def _max_intents(self) -> int:
        try:
            n = int((self._cfg.get("nlu") or {}).get("max_intents", 5))
        except (TypeError, ValueError):
            n = 5
        return max(1, min(8, n))

    def _thinking_enabled(self) -> bool:
        return bool((self._cfg.get("llm") or {}).get("thinking_enabled", False))

    def _max_tokens(self, thinking: bool) -> int:
        llm = self._cfg.get("llm") or {}
        key = "thinking_max_tokens" if thinking else "max_tokens"
        default = 4096 if thinking else 512
        try:
            n = int(llm.get(key, default))
        except (TypeError, ValueError):
            n = default
        return max(64, n)

    def _timeout_s(self, thinking: bool) -> float:
        if thinking:
            return max(self._timeout, 30.0)
        return self._timeout

    async def extract(self, text: str) -> list | None:
        """返回 [{"intent": str, "slots": dict}, ...]（≤max_intents）；失败/不可用返回 None。"""
        if not self.enabled:
            return None
        content = await self.raw_content(text)
        if content is None:
            return None
        return self._parse(content)

    async def _chat(
        self,
        text: str,
        model_id: str | None = None,
        *,
        thinking: bool | None = None,
        max_tokens: int | None = None,
        allow_length_retry: bool = True,
    ) -> tuple[str | None, str | None]:
        ep = resolve_llm(self._cfg, model_id)
        if ep is None:
            self._warn_once("[forkai-core] llm 未配置 key，整句不执行")
            return None, "未配置key"
        base_url, api_key, model = ep
        use_thinking = self._thinking_enabled() if thinking is None else thinking
        tokens = max_tokens if max_tokens is not None else self._max_tokens(use_thinking)
        body: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt(self._max_intents())},
                {"role": "user", "content": text},
            ],
            "temperature": 0,
            "max_tokens": tokens,
        }
        item = catalog_item(model_id or current_llm_model(self._cfg))
        provider = item["provider"] if item else "deepseek"
        if provider == "siliconflow":
            body["enable_thinking"] = use_thinking
        else:
            body["thinking"] = {"type": "enabled" if use_thinking else "disabled"}
        try:
            res = await self._client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=body,
                timeout=self._timeout_s(use_thinking),
            )
        except httpx.TimeoutException:
            self._warn_once("[forkai-core] llm 超时，整句不执行")
            return None, "超时"
        except httpx.HTTPError as e:
            self._warn_once(f"[forkai-core] llm 不可达，整句不执行: {e}")
            return None, "失败"
        if res.status_code == 401:
            self._warn_once("[forkai-core] llm 401，官方 key 无效")
            return None, "key无效"
        if not 200 <= res.status_code < 300:
            self._warn_once(f"[forkai-core] llm HTTP {res.status_code}")
            return None, f"HTTP{res.status_code}"
        try:
            payload = res.json()
            choice = payload["choices"][0]
            msg = choice.get("message") or {}
            content = msg.get("content")
            if content is None:
                content = ""
            reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
            finish = str(choice.get("finish_reason") or "")
        except (KeyError, IndexError, TypeError, ValueError) as e:
            self._warn_once(f"[forkai-core] llm 输出无法解析: {e}")
            return None, "失败"
        print(
            f"[forkai-core] llm thinking={'on' if use_thinking else 'off'} "
            f"finish={finish or '?'} content_len={len(content)} "
            f"reasoning_len={len(reasoning)} max_tokens={tokens}",
            flush=True,
        )
        if use_thinking and not str(content).strip() and finish == "length" and allow_length_retry:
            bigger = min(max(tokens * 2, tokens + 1024), 8192)
            if bigger > tokens:
                print(
                    f"[forkai-core] llm 思考占满额度，重试 max_tokens={bigger}",
                    flush=True,
                )
                return await self._chat(
                    text,
                    model_id,
                    thinking=use_thinking,
                    max_tokens=bigger,
                    allow_length_retry=False,
                )
        if not str(content).strip():
            return None, "空content"
        return content, None

    async def probe_model(self, model_id: str) -> dict:
        item = catalog_item(model_id) or {"id": model_id, "name": model_id}
        t0 = time.perf_counter()
        content, err = await self._chat("前进", model_id, thinking=False)
        if content is None:
            return {
                "id": model_id,
                "name": item["name"],
                "latencyMs": None,
                "error": err or "失败",
            }
        ms = int((time.perf_counter() - t0) * 1000)
        return {"id": model_id, "name": item["name"], "latencyMs": ms, "error": None}

    async def raw_content(self, text: str, model_id: str | None = None) -> str | None:
        content, _err = await self._chat(text, model_id)
        return content

    def _warn_once(self, msg: str) -> None:
        if not self._warned_unreachable:
            print(msg)
            self._warned_unreachable = True

    def reset_warn(self) -> None:
        self._warned_unreachable = False

    def _parse(self, content: str) -> list | None:
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
        max_n = self._max_intents()
        out = []
        for item in obj["intents"]:
            if not isinstance(item, dict):
                continue
            intent = item.get("intent")
            slots = item.get("slots")
            if not isinstance(intent, str) or not intent:
                continue
            out.append({"intent": intent, "slots": slots if isinstance(slots, dict) else {}})
            if len(out) >= max_n:
                break
        return out

    async def close(self) -> None:
        await self._client.aclose()
