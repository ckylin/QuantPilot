"""Unified LLM provider factory.

DeepSeek is the default and is OpenAI-compatible, so it reuses
``OpenAICompatibleProvider``. Other vendors are added by registering one entry
in ``PROVIDER_PRESETS`` — no changes to Stage A/B logic.

Design:
    - api_key comes from QP_LLM_API_KEY (never hard-coded).
- If api_key is empty, fall back to OfflineProvider so the whole chain stays
  runnable offline (tests / demo without credentials).
- A "model" override is always allowed (default deepseek-flash).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.services.llm.provider import (
    LLMProvider,
    OfflineProvider,
    OpenAICompatibleProvider,
)


@dataclass(frozen=True)
class ProviderPreset:
    name: str
    default_base_url: str
    # 该供应商已知的默认模型（仅作提示/回退，实际以配置 llm_model 为准）
    known_models: tuple[str, ...] = ()


# 统一注册表：新增供应商 = 加一条 preset。全部走 OpenAI 兼容 /chat/completions。
PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    "deepseek": ProviderPreset(
        name="deepseek",
        default_base_url="https://api.deepseek.com/v1",
        known_models=("deepseek-flash", "deepseek-chat", "deepseek-reasoner"),
    ),
    "openai": ProviderPreset(
        name="openai",
        default_base_url="https://api.openai.com/v1",
        known_models=("gpt-4o-mini", "gpt-4o"),
    ),
    "qwen": ProviderPreset(
        name="qwen",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        known_models=("qwen-turbo", "qwen-plus", "qwen-max"),
    ),
    # custom: 由 QP_LLM_BASE_URL 指定任意兼容端点
    "custom": ProviderPreset(name="custom", default_base_url=""),
}


def register_provider(preset: ProviderPreset) -> None:
    """扩展点：运行时注册新供应商 preset。"""
    PROVIDER_PRESETS[preset.name] = preset


def create_provider(
    *,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    allow_mock: bool = True,
) -> LLMProvider:
    """按配置创建 Provider。

    - provider/model/api_key/base_url 缺省时读全局 settings。
    - api_key 为空且 allow_mock=True → OfflineProvider（离线可跑）。
    - api_key 为空且 allow_mock=False → ValueError（生产强制要求凭证）。
    """
    name = (provider or settings.llm_provider).lower()
    key = api_key if api_key is not None else settings.llm_api_key
    model_name = model or settings.llm_model

    if not key:
        if allow_mock:
            return OfflineProvider()
        raise ValueError(f"未提供 LLM API Key（QP_LLM_API_KEY），provider={name}")

    preset = PROVIDER_PRESETS.get(name)
    if preset is None:
        # 未知 provider 也允许：只要给了 base_url，就当 custom 兼容端点处理
        preset = PROVIDER_PRESETS["custom"]

    url = (base_url or settings.llm_base_url or preset.default_base_url).rstrip("/")
    if not url:
        raise ValueError(f"provider={name} 缺少 base_url，请设置 QP_LLM_BASE_URL")

    return OpenAICompatibleProvider(
        name=name,
        model=model_name,
        api_key=key,
        base_url=url,
        timeout=settings.request_timeout,
    )


def get_provider() -> LLMProvider:
    """单例入口（供编排层调用）。"""
    global _provider
    if _provider is None:
        _provider = create_provider()
    return _provider


_provider: LLMProvider | None = None


def reset_provider() -> None:
    """测试用：清空单例。"""
    global _provider
    _provider = None
