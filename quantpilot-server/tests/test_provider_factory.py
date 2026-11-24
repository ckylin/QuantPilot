"""Tests for the unified LLM provider factory (DeepSeek default, mock fallback)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.services.llm.provider import OfflineProvider, OpenAICompatibleProvider
from app.services.llm.provider_factory import (
    PROVIDER_PRESETS,
    ProviderPreset,
    create_provider,
    register_provider,
)
from app.schemas import StrategySpec
import json


def test_default_provider_is_deepseek_flash():
    assert settings.llm_provider == "deepseek"
    assert settings.llm_model == "deepseek-flash"
    assert "deepseek.com" in settings.llm_base_url


def test_no_key_falls_back_to_offline():
    p = create_provider(api_key="")
    assert isinstance(p, OfflineProvider)


def test_no_key_strict_raises():
    with pytest.raises(ValueError, match="QP_LLM_API_KEY"):
        create_provider(api_key="", allow_mock=False)


def test_deepseek_provider_construction():
    p = create_provider(provider="deepseek", model="deepseek-flash",
                        api_key="sk-test", base_url="https://api.deepseek.com/v1")
    assert isinstance(p, OpenAICompatibleProvider)
    assert p.name == "deepseek"
    assert p.model == "deepseek-flash"
    assert p.base_url == "https://api.deepseek.com/v1"


def test_other_vendors_present():
    for name in ("deepseek", "openai", "qwen", "custom"):
        assert name in PROVIDER_PRESETS


def test_register_custom_provider():
    register_provider(ProviderPreset(name="test_vendor", default_base_url="https://x.example/v1"))
    p = create_provider(provider="test_vendor", api_key="k", base_url="https://x.example/v1")
    assert p.name == "test_vendor"


def test_unknown_provider_treated_as_custom():
    p = create_provider(provider="unknown", api_key="k", base_url="https://y.example/v1")
    assert isinstance(p, OpenAICompatibleProvider)
    assert p.name == "unknown"


def test_offline_provider_emits_valid_spec():
    p = OfflineProvider()
    raw = p.generate(system="你是策略规格编译器", user="中证500里波动最小的30只，每周调仓")
    spec = StrategySpec.model_validate(json.loads(raw))
    assert spec.universe.name == "csi500"
    assert spec.portfolio.topk == 30
    assert spec.rebalance.freq == "week"


def test_offline_provider_model_intent():
    p = OfflineProvider()
    raw = p.generate(system="你是策略规格编译器", user="用机器学习预测沪深300未来收益")
    spec = StrategySpec.model_validate(json.loads(raw))
    assert spec.signal.type == "model"
    assert spec.signal.model.type == "lgbm"
