"""Tests for the Spec v0.1 Pydantic model and its 9 validation rules."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from app.schemas import StrategySpec


def test_valid_spec(base_spec):
    spec = StrategySpec.model_validate(base_spec)
    assert spec.spec_version == "0.1"
    assert spec.portfolio.n_drop == 4          # default max(1, 20//5)
    assert spec.rebalance.monthday == 1        # default for freq=month
    assert spec.cost.open_cost == 0.001
    assert spec.account.initial_capital == 100_000_000


def test_universe_conditional_required(base_spec):
    bad = copy.deepcopy(base_spec)
    bad["universe"] = {"type": "builtin"}
    with pytest.raises(ValidationError, match="name 必填"):
        StrategySpec.model_validate(bad)

    bad = copy.deepcopy(base_spec)
    bad["universe"] = {"type": "custom"}
    with pytest.raises(ValidationError, match="custom_symbols 必填"):
        StrategySpec.model_validate(bad)


def test_symbol_format(base_spec):
    bad = copy.deepcopy(base_spec)
    bad["universe"] = {"type": "custom", "custom_symbols": ["600519", "SZ000858"]}
    with pytest.raises(ValidationError, match="股票代码格式错误"):
        StrategySpec.model_validate(bad)


def test_date_order_and_no_overlap(base_spec):
    bad = copy.deepcopy(base_spec)
    bad["date_range"]["start"] = "2025-01-01"
    with pytest.raises(ValidationError, match="不得晚于"):
        StrategySpec.model_validate(bad)

    bad = copy.deepcopy(base_spec)
    bad["date_range"]["in_sample"] = {"start": "2021-01-01", "end": "2023-12-31"}
    bad["date_range"]["out_sample"] = {"start": "2023-01-01", "end": "2024-12-31"}
    with pytest.raises(ValidationError, match="不得重叠"):
        StrategySpec.model_validate(bad)


def test_topk_capacity_and_n_drop(base_spec):
    bad = copy.deepcopy(base_spec)
    bad["portfolio"]["topk"] = 400  # > csi300 capacity
    with pytest.raises(ValidationError, match="超过标的池容量"):
        StrategySpec.model_validate(bad)

    bad = copy.deepcopy(base_spec)
    bad["portfolio"]["n_drop"] = 25
    with pytest.raises(ValidationError, match="n_drop 不得大于 topk"):
        StrategySpec.model_validate(bad)


def test_risk_consistency_normalization(base_spec):
    base_spec["risk"] = {"max_single_weight": 0.03}
    base_spec["portfolio"]["max_single_weight"] = 0.08
    spec = StrategySpec.model_validate(base_spec)
    assert spec.portfolio.max_single_weight == 0.03
    assert spec.risk.max_single_weight == 0.03
    assert spec.effective_max_single_weight == 0.03


def test_expression_whitelist(base_spec):
    bad = copy.deepcopy(base_spec)
    bad["signal"]["expression"] = "eval('1+1')"
    with pytest.raises(ValidationError, match="未知算子/标识符"):
        StrategySpec.model_validate(bad)

    bad = copy.deepcopy(base_spec)
    bad["signal"]["expression"] = "$unknown / Ref($close, 5)"
    with pytest.raises(ValidationError, match="未知字段"):
        StrategySpec.model_validate(bad)


def test_valid_complex_expression(base_spec):
    base_spec["signal"]["expression"] = "Rank($close/Ref($close,20)-1) + Rank(-Std($close,20)/Mean($close,20))"
    spec = StrategySpec.model_validate(base_spec)
    assert spec.signal.expression.startswith("Rank(")


def test_unknown_fields_rejected(base_spec):
    bad = copy.deepcopy(base_spec)
    bad["unexpected_field"] = 123
    with pytest.raises(ValidationError):
        StrategySpec.model_validate(bad)


def test_conditional_signal_expression(base_spec):
    bad = copy.deepcopy(base_spec)
    bad["signal"] = {"type": "expression", "direction": "desc"}
    with pytest.raises(ValidationError, match="expression 必填"):
        StrategySpec.model_validate(bad)
