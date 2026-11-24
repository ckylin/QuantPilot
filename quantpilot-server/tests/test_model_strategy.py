"""Tests for qlib prediction-model strategy support (Spec + codegen + AST)."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from app.schemas import StrategySpec
from app.services.llm.ast_guard import check_code
from app.services.llm.codegen_service import CodeGenerator
from app.services.llm.prompt_loader import PromptLoader


@pytest.fixture
def model_spec_dict() -> dict:
    return {
        "spec_version": "0.1",
        "meta": {"name": "LGBM预测Top20", "intent": "用机器学习预测沪深300未来收益", "tags": ["multi_factor"]},
        "universe": {"type": "builtin", "name": "csi300"},
        "date_range": {"start": "2018-01-01", "end": "2024-12-31"},
        "rebalance": {"freq": "month"},
        "signal": {
            "type": "model",
            "model": {
                "type": "lgbm",
                "features": "alpha158",
                "segments": {
                    "train": ["2018-01-01", "2021-12-31"],
                    "valid": ["2022-01-01", "2022-12-31"],
                    "test": ["2023-01-01", "2024-12-31"],
                },
                "params": {"learning_rate": 0.05, "num_leaves": 31},
            },
        },
        "portfolio": {"selection": "topk", "topk": 20, "weighting": "equal"},
    }


def test_model_spec_valid(model_spec_dict):
    spec = StrategySpec.model_validate(model_spec_dict)
    assert spec.signal.type == "model"
    assert spec.signal.model is not None
    assert spec.signal.model.type == "lgbm"
    assert spec.signal.model.features == "alpha158"
    assert spec.signal.direction == "desc"  # 模型恒为 desc
    assert spec.signal.model.segments.test[0].isoformat() == "2023-01-01"


def test_model_segments_must_be_ordered(model_spec_dict):
    bad = copy.deepcopy(model_spec_dict)
    bad["signal"]["model"]["segments"]["train"] = ["2022-01-01", "2023-12-31"]
    with pytest.raises(ValidationError, match="严格递增"):
        StrategySpec.model_validate(bad)


def test_model_segments_within_date_range(model_spec_dict):
    bad = copy.deepcopy(model_spec_dict)
    bad["signal"]["model"]["segments"]["test"] = ["2025-01-01", "2025-12-31"]
    with pytest.raises(ValidationError, match="必须落在 date_range 内"):
        StrategySpec.model_validate(bad)


def test_custom_features_require_signals(model_spec_dict):
    bad = copy.deepcopy(model_spec_dict)
    bad["signal"]["model"]["features"] = "custom"
    with pytest.raises(ValidationError, match="signals 必填"):
        StrategySpec.model_validate(bad)

    ok = copy.deepcopy(model_spec_dict)
    ok["signal"]["model"]["features"] = "custom"
    ok["signal"]["model"]["signals"] = ["$close / Ref($close, 20) - 1", "Std($close, 20)"]
    spec = StrategySpec.model_validate(ok)
    assert spec.signal.model.features == "custom"


def test_custom_signals_whitelist(model_spec_dict):
    bad = copy.deepcopy(model_spec_dict)
    bad["signal"]["model"]["features"] = "custom"
    bad["signal"]["model"]["signals"] = ["eval('1')"]
    with pytest.raises(ValidationError, match="非法"):
        StrategySpec.model_validate(bad)


def test_codegen_selects_model_template(model_spec_dict):
    spec = StrategySpec.model_validate(model_spec_dict)
    prompts = PromptLoader()
    gen = CodeGenerator(None, prompts)

    expr_code = gen.render_deterministic(StrategySpec.model_validate({
        **{k: v for k, v in model_spec_dict.items() if k != "signal"},
        "signal": {"type": "expression", "expression": "$close / Ref($close, 20) - 1", "direction": "desc"},
    }))
    model_code = gen.render_deterministic(spec)

    assert "EXPRESSION" in expr_code
    assert "LGBModel" in model_code
    assert "Alpha158" in model_code
    assert "DatasetH" in model_code
    assert "TopkDropoutStrategy" in model_code
    assert "model.fit(dataset)" in model_code
    assert "model.predict(dataset)" in model_code
    assert "pred_score" in model_code
    assert "QUANTPLOT_RESULT" in model_code


def test_model_code_passes_ast_and_compiles(model_spec_dict):
    spec = StrategySpec.model_validate(model_spec_dict)
    prompts = PromptLoader()
    code = CodeGenerator(None, prompts).render_deterministic(spec)

    passed, errors = check_code(code, prompts.code_template("model"))
    assert passed, errors

    compile_code = code.replace("{{TASK_ID}}", "t").replace("{{LOGS_REF}}", "l")
    compile(compile_code, "model_workflow", "exec")


def test_model_code_has_no_expression_placeholders(model_spec_dict):
    """模型模板只需要 SPEC_DICT，不应残留其它占位符。"""
    spec = StrategySpec.model_validate(model_spec_dict)
    code = CodeGenerator(None, PromptLoader()).render_deterministic(spec)
    for placeholder in ("{{EXPRESSION}}", "{{FILTERS}}", "{{STRATEGY_KWARGS}}", "{{SPEC_DICT}}"):
        assert placeholder not in code


def test_model_custom_code_uses_qlib_dataloader(model_spec_dict):
    conf = copy.deepcopy(model_spec_dict)
    conf["signal"]["model"]["features"] = "custom"
    conf["signal"]["model"]["signals"] = ["$close / Ref($close, 20) - 1"]
    conf["signal"]["model"]["label"] = "Ref($close,-2)/Ref($close,-1)-1"
    spec = StrategySpec.model_validate(conf)
    code = CodeGenerator(None, PromptLoader()).render_deterministic(spec)
    # custom 分支用 QlibDataLoader + config（feature/label），而非内置 get_feature_config()
    assert "QlibDataLoader" in code
    assert '{"config": {"feature":' in code
    assert "feature_names" in code  # 自定义信号在运行时命名成 F0/F1...
    assert "$close / Ref($close, 20) - 1" in code  # 自定义信号写入了 SPEC
