"""LLM provider abstraction.

Swap models without touching Stage A/B logic. ``MockProvider`` is used by the
regression tests so the whole chain can be validated offline.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

import httpx


class LLMProvider(Protocol):
    name: str

    def generate(
        self,
        *,
        system: str,
        user: str,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str: ...


class OpenAICompatibleProvider:
    """Works with any OpenAI-compatible /chat/completions endpoint."""

    def __init__(self, *, name: str, model: str, api_key: str, base_url: str, timeout: float = 60.0):
        self.name = name
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def generate(
        self,
        *,
        system: str,
        user: str,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            payload["response_format"] = response_format

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
            data = response.json()
        return data["choices"][0]["message"]["content"]


class OfflineProvider:
    """Fully offline deterministic provider used when no API key is configured.

    Unlike MockProvider (which requires preloaded responses for tests), this
    provider synthesizes a valid Spec / code from the user intent so the HTTP
    API can demo end-to-end without credentials. It is intentionally simple —
    production always uses a real provider.
    """

    name = "offline"

    @staticmethod
    def _spec_from_intent(intent: str) -> dict[str, Any]:
        import re
        from datetime import date

        this_year = date.today().year
        # 标的池
        if "中证500" in intent or "500" in intent:
            universe = "csi500"
        elif "中证1000" in intent or "1000" in intent:
            universe = "csi1000"
        else:
            universe = "csi300"
        # 持仓数
        m = re.search(r"(\d+)\s*只", intent)
        topk = min(50, int(m.group(1))) if m else 20
        # 调仓频率
        if "每周" in intent or "周调" in intent:
            rebalance = {"freq": "week", "weekday": 1}
        elif "每天" in intent or "每日" in intent:
            rebalance = {"freq": "day"}
        else:
            rebalance = {"freq": "month", "monthday": 1}
        # 方向/信号
        looks_low = bool(re.search(r"跌|超跌|反弹|低估|波动小|最稳|低波|价值", intent))
        is_model = bool(re.search(r"机器学习|AI预测|训练模型|预测未来|智能预测|lightgbm", intent, re.I))
        start = f"{this_year - 4}-01-01"
        mid = f"{this_year - 2}-12-31"
        test_start = f"{this_year - 1}-01-01"
        end = f"{this_year}-12-31"
        if is_model:
            signal: dict[str, Any] = {
                "type": "model",
                "model": {
                    "type": "lgbm",
                    "features": "alpha158",
                    "segments": {"train": [start, mid], "test": [test_start, end]},
                },
            }
            tags = ["multi_factor"]
            name = "AI预测策略"
        else:
            expression = ("-( $close / Ref($close, 20) - 1 )" if looks_low
                          else "$close / Ref($close, 20) - 1")
            signal = {
                "type": "expression",
                "expression": expression,
                "direction": "asc" if looks_low else "desc",
            }
            tags = ["reversal"] if looks_low else ["momentum"]
            name = "超跌反弹策略" if looks_low else "动量策略"
        return {
            "spec_version": "0.1",
            "meta": {"name": name, "intent": intent, "tags": tags},
            "universe": {"type": "builtin", "name": universe},
            "date_range": {"start": start, "end": end},
            "rebalance": rebalance,
            "signal": signal,
            "portfolio": {"selection": "topk", "topk": topk, "weighting": "equal"},
            "benchmark": "csi300",
        }

    def generate(
        self,
        *,
        system: str,
        user: str,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        if "想法理解器" in system:
            # 零代码入口：离线时用规则版解析，保证没有 key 也能跑通引导流程
            from app.services.intent.rules import heuristic_frame
            return json.dumps(heuristic_frame(user).model_dump(mode="json"), ensure_ascii=False)
        if "策略规格编译器" in system:
            return json.dumps(self._spec_from_intent(user), ensure_ascii=False)
        if "代码填充器" in system:
            # Stage B 的 render_deterministic 是主路径；离线 provider 返回空，
            # 由 CodeGenerator 的确定性渲染兜底（见 orchestration）。
            return ""
        return ""


class MockProvider:
    """Deterministic provider for offline tests.

    Pass the desired Spec dict and/or a full Python code string. It detects
    which stage is calling by looking at the system prompt signature.
    """

    name = "mock"

    def __init__(
        self,
        *,
        spec_response: dict[str, Any] | None = None,
        code_response: str | None = None,
        frame_response: dict[str, Any] | None = None,
    ):
        self.spec_response = spec_response
        self.code_response = code_response
        self.frame_response = frame_response

    def generate(
        self,
        *,
        system: str,
        user: str,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> str:
        if "想法理解器" in system:
            if self.frame_response is None:
                raise RuntimeError("MockProvider 未配置 frame_response")
            return json.dumps(self.frame_response, ensure_ascii=False)
        if "策略规格编译器" in system:
            if self.spec_response is None:
                raise RuntimeError("MockProvider 未配置 spec_response")
            return json.dumps(self.spec_response, ensure_ascii=False)
        if "代码填充器" in system:
            if self.code_response is None:
                raise RuntimeError("MockProvider 未配置 code_response")
            return self.code_response
        raise RuntimeError("MockProvider 无法识别的 prompt 类型")
