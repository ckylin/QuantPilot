"""Tests for the Model Zoo — registry, parameterized codegen, AST, recommendation, benchmark."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import (
    Benchmark,
    ModelPerformance,
    ModelRegistry,
    ModelService,
    ModelSpec,
    PromotionRule,
    seed_models,
)
from app.models.evaluation import ModelRunner
from app.schemas import StrategySpec
from app.services.llm.ast_guard import check_code
from app.services.llm.codegen_service import CodeGenerator
from app.services.llm.prompt_loader import PromptLoader


@pytest.fixture()
def registry() -> ModelRegistry:
    r = ModelRegistry()
    seed_models(r)
    return r


def make_spec(model_id: str, params: dict | None = None) -> StrategySpec:
    model = {"model_id": model_id, "features": "alpha158",
             "segments": {"train": ["2018-01-01", "2021-06-30"],
                          "test": ["2023-01-01", "2024-12-31"]}}
    if params is not None:
        model["params"] = params
    return StrategySpec.model_validate({
        "spec_version": "0.1",
        "meta": {"name": "预测策略", "intent": "用机器学习预测沪深300未来收益"},
        "universe": {"type": "builtin", "name": "csi300"},
        "date_range": {"start": "2018-01-01", "end": "2024-12-31"},
        "rebalance": {"freq": "month"},
        "signal": {"type": "model", "model": model},
        "portfolio": {"selection": "topk", "topk": 20, "weighting": "equal"},
    })


# ---- 注册表契约 -----------------------------------------------------------

def test_seed_models_registered(registry):
    assert len(registry.list()) == 7
    assert set(m.model_id for m in registry.available() if not m.requires_gpu) == {
        "lgbm", "xgboost", "catboost", "linear", "double_ensemble"
    }


def test_constructor_must_have_params_placeholder():
    with pytest.raises(ValueError):
        ModelSpec(model_id="bad", name="bad", category="gbdt",
                  imports=["import x"], constructor="Foo()")


def test_imports_validated():
    with pytest.raises(ValueError):
        ModelSpec(model_id="bad", name="bad", category="gbdt",
                  imports=["__import__('os')"], constructor="Foo(**{params})")


def test_build_constructor_filters_params(registry):
    m = registry.require("lgbm")
    code = m.build_constructor({"num_leaves": 63, "evil": "__import__('os')"})
    assert "num_leaves': 63" in code
    assert "evil" not in code  # 非白名单参数被丢弃


def test_dynamic_allowed_imports(registry):
    mods = registry.allowed_imports()
    assert "qlib.contrib.model.gbdt" in mods
    assert "qlib.contrib.model.pytorch_lstm" in mods
    assert len(mods) == 7


# ---- 参数化代码生成 --------------------------------------------------------

@pytest.mark.parametrize("model_id,ctor", [
    ("lgbm", "LGBModel"),
    ("xgboost", "XGBModel"),
    ("catboost", "CatBoostModel"),
    ("linear", "LinearModel"),
    ("lstm", "LSTMModel"),
    ("transformer", "TransformerModel"),
])
def test_deterministic_codegen_for_each_model(model_id, ctor):
    code = CodeGenerator(None, PromptLoader()).render_deterministic(make_spec(model_id))
    compile(code.replace("{{TASK_ID}}", "t").replace("{{LOGS_REF}}", "l"), "m", "exec")
    assert "def build_model" in code
    assert f"return {ctor}(" in code
    assert code.count("import ") >= 1


def test_ast_accepts_registered_model_imports():
    code = CodeGenerator(None, PromptLoader()).render_deterministic(make_spec("xgboost"))
    # 不传 extra_imports 时，注册表外的 xgboost import 应被拒
    passed_default, errs = check_code(code, PromptLoader().code_template("model"))
    assert not passed_default
    # 传注册表派生的白名单后通过
    extra = ModelService().allowed_imports()
    passed, errors = check_code(code, PromptLoader().code_template("model"), extra_imports=extra)
    assert passed, errors


def test_new_model_registration_needs_no_template_change(registry):
    """核心保证：注册一个全新模型即可生成合法代码，不改模板/AST。"""
    registry.register(ModelSpec(
        model_id="my_custom", name="自定义模型", category="custom",
        imports=["from qlib.contrib.model.gbdt import LGBModel"],
        constructor="LGBModel(**{params})",
        default_params={"loss": "mse"},
    ))
    from app.models import set_registry
    old = None
    try:
        from app.models import get_registry
        old = get_registry()
        set_registry(registry)
        code = CodeGenerator(None, PromptLoader()).render_deterministic(make_spec("my_custom"))
        extra = ModelService().allowed_imports()
        passed, errs = check_code(code, PromptLoader().code_template("model"), extra_imports=extra)
        assert passed, errs
        assert "my_custom" in code  # model_id 写进 SPEC
    finally:
        if old is not None:
            set_registry(old)


# ---- 推荐 -----------------------------------------------------------------

def test_recommend_respects_gpu_constraint(registry):
    svc = ModelService(registry)
    recs = svc.recommend("深度学习预测", no_gpu=True)
    assert all(not m.requires_gpu for m in recs)


def test_recommend_small_data_prefers_lightweight(registry):
    svc = ModelService(registry)
    recs = svc.recommend("快速预测", dataset_size=2000, no_gpu=True)
    assert "lgbm" in [m.model_id for m in recs] or "linear" in [m.model_id for m in recs]


def test_auto_select_falls_back(registry):
    svc = ModelService(registry)
    assert svc.auto_select("随便预测").model_id == "lgbm"


def test_prompt_catalog_contains_models():
    text = PromptLoader().system("intent2spec", intent="x", structured_params="",
                                 default_date_range="2020-2024")
    assert "lgbm" in text
    assert "【可用预测模型】" in text


# ---- 基准评测 --------------------------------------------------------------

class FakeRunner(ModelRunner):
    def __init__(self, ic=0.03, icstd=0.06, seconds=10):
        self.ic, self.icstd, self.seconds = ic, icstd, seconds

    def run(self, model_id, dataset_ref, params=None):
        return ModelPerformance(ic_mean=self.ic, ic_std=self.icstd,
                                fit_seconds=self.seconds)


def test_benchmark_promotes_passing_model(registry):
    b = Benchmark(registry, FakeRunner(ic=0.03, icstd=0.06))
    assert registry.get("catboost").status == "beta"
    ok, perf, reasons = b.evaluate("catboost")
    assert ok and not reasons
    assert registry.get("catboost").status == "active"


def test_benchmark_rejects_bad_model(registry):
    b = Benchmark(registry, FakeRunner(ic=-0.01, icstd=0.1))  # IR 为负
    ok, perf, reasons = b.evaluate("catboost")
    assert not ok
    assert any("IC" in r or "ICIR" in r for r in reasons)


def test_benchmark_deprecates_failing_active_model(registry):
    # 先让它 active
    Benchmark(registry, FakeRunner()).evaluate("catboost")
    assert registry.get("catboost").status == "active"
    # 再评测不达标 → 下架
    b = Benchmark(registry, FakeRunner(ic=-0.02, icstd=0.1))
    b.evaluate("catboost")
    assert registry.get("catboost").status == "deprecated"


def test_benchmark_rank(registry):
    b = Benchmark(registry, FakeRunner())
    ranked = b.rank()
    assert ranked[0][0] == "lgbm"  # ICIR 0.55 最高
