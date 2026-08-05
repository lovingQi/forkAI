"""LLMClient：调 llama.cpp llama-server 的 OpenAI 兼容接口做意图抽取（步骤25）。

- POST {base_url}/v1/chat/completions，messages=[system, user]，temperature=0
- 剥 ```json 围栏 → json.loads → 结构校验（每项 intent 字符串 + slots dict）→ 返回
- 超时/异常/解析失败返回 None（记日志，不抛出）；服务不可达打一次 warning 后静默降级
"""
import json
import re

import httpx

from .prompts import SYSTEM_PROMPT

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class LLMClient:
    def __init__(self, cfg: dict):
        llm_cfg = cfg.get("llm", {}) or {}
        self._base_url = str(llm_cfg.get("base_url", "http://127.0.0.1:19002")).rstrip("/")
        self._timeout = float(llm_cfg.get("timeout_s", 3))
        self.enabled = bool(llm_cfg.get("enabled", False))
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._timeout))
        self._warned_unreachable = False

    async def extract(self, text: str) -> list | None:
        """返回 [{"intent": str, "slots": dict}, ...]（≤3 项）；失败/不可用返回 None。"""
        if not self.enabled:
            return None
        try:
            res = await self._client.post(
                f"{self._base_url}/v1/chat/completions",
                json={
                    "model": "qwen2-0.5b-instruct",
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": text},
                    ],
                    "temperature": 0,
                    "max_tokens": 256,
                },
            )
            content = res.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError) as e:
            self._warn_once(f"[forkai-core] llm 不可达，降级纯规则 NLU: {e}")
            return None
        return self._parse(content)

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
