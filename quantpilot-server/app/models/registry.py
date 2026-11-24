"""ModelRegistry — declarative model catalog, the "add a model = add a declaration" surface.

Adding a model here is enough to make it generatable, AST-allowed and recommendable.
No edits to the code template, AST guard, or (for CPU models) the sandbox image.
"""

from __future__ import annotations

import threading
from typing import Iterable

from app.models.schemas import ModelCategory, ModelPerformance, ModelSpec, ModelStatus


class ModelRegistry:
    def __init__(self):
        self._models: dict[str, ModelSpec] = {}
        self._lock = threading.RLock()

    def register(self, spec: ModelSpec) -> str:
        with self._lock:
            self._models[spec.model_id] = spec
        return spec.model_id

    def get(self, model_id: str) -> ModelSpec | None:
        with self._lock:
            return self._models.get(model_id)

    def require(self, model_id: str) -> ModelSpec:
        m = self.get(model_id)
        if m is None:
            raise KeyError(f"模型未注册: {model_id}")
        return m

    def list(self, category: ModelCategory | None = None,
             status: ModelStatus | None = None,
             require_gpu: bool | None = None) -> list[ModelSpec]:
        with self._lock:
            items = list(self._models.values())
        if category is not None:
            items = [m for m in items if m.category == category]
        if status is not None:
            items = [m for m in items if m.status == status]
        if require_gpu is not None:
            items = [m for m in items if m.requires_gpu == require_gpu]
        return items

    def available(self) -> list[ModelSpec]:
        """可用于生成的模型：非 deprecated。"""
        return [m for m in self.list() if m.status != "deprecated"]

    def allowed_imports(self) -> set[str]:
        """所有注册模型声明的 import 模块 —— AST 白名单从此动态派生。"""
        mods: set[str] = set()
        for m in self._models.values():
            mods.update(m.import_modules)
        return mods

    def required_pip_packages(self, model_ids: Iterable[str]) -> set[str]:
        """给定模型所需的额外 pip 包（供镜像构建/检查）。"""
        pkgs: set[str] = set()
        for mid in model_ids:
            m = self.get(mid)
            if m:
                pkgs.update(m.pip_packages)
        return pkgs

    # ---- 推荐：按场景挑模型 ------------------------------------------------

    def recommend(self, intent: str = "", dataset_size: int | None = None,
                  no_gpu: bool = True, top: int = 3) -> list[ModelSpec]:
        """按意图/数据规模/资源约束推荐模型。

        打分 = 绩效(ICIR) + 场景匹配 + 数据量适配 - 资源成本。
        结果注入 Stage A prompt，让 LLM 有依据地选模，而非随机。
        """
        cands = [m for m in self.available() if not (no_gpu and m.requires_gpu)]
        if dataset_size is not None:
            cands = [m for m in cands if dataset_size >= m.min_dataset_rows]

        scored: list[tuple[float, ModelSpec]] = []
        for m in cands:
            perf = m.performance
            score = (perf.ir or 0.0) * 2.0 + (perf.ic_mean or 0.0)
            # 场景标签匹配
            intent_l = intent.lower()
            for tag in m.suitable_for:
                if tag.replace("_", " ") in intent_l or tag in intent_l:
                    score += 0.3
            # 数据量小 → 偏好轻量
            if dataset_size is not None and dataset_size < 5000 and "small_data" in m.suitable_for:
                score += 0.4
            # 训练速度作为轻微加分（demo 体验）
            if perf.fit_seconds is not None:
                score += max(0, 0.2 - perf.fit_seconds / 600)
            scored.append((score, m))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored[:top]]

    def prompt_catalog(self, no_gpu: bool = True) -> str:
        """生成可注入 Stage A prompt 的模型目录文本。"""
        lines = ["可用预测模型目录（signal.model.type 选对应 model_id）："]
        for m in self.available():
            if no_gpu and m.requires_gpu:
                continue
            perf = m.performance
            ic = f"{perf.ic_mean:+.3f}" if perf.ic_mean is not None else "待测"
            ir = f"{perf.ir:.2f}" if perf.ir is not None else "待测"
            gpu = " · 需GPU" if m.requires_gpu else ""
            lines.append(
                f"- {m.model_id} [{m.name}] 类别={m.category} IC={ic} ICIR={ir}{gpu}"
                f" :: {m.description}"
            )
        return "\n".join(lines)

    def clear(self) -> None:
        with self._lock:
            self._models.clear()


_registry: ModelRegistry | None = None


def get_registry() -> ModelRegistry:
    global _registry
    if _registry is None:
        _registry = ModelRegistry()
        seed_models(_registry)
    return _registry


def set_registry(registry: ModelRegistry) -> None:
    global _registry
    _registry = registry
    # 让服务单例感知新 registry（避免持有旧引用）
    from app.models import service as service_mod
    if service_mod._service is not None:
        service_mod._service.registry = registry


def seed_models(registry: ModelRegistry) -> None:
    """预置 qlib 0.9.7 确实存在的模型。绩效为示意先验，待基准评测回填。"""
    if registry.list():
        return

    # GBDT 类：轻量、CPU、数据量大效果好 —— demo 主力
    registry.register(ModelSpec(
        model_id="lgbm", name="LightGBM", category="gbdt", status="active",
        description="梯度提升树，表格因子效果稳定、训练快，默认首选。",
        imports=["from qlib.contrib.model.gbdt import LGBModel"],
        constructor="LGBModel(**{params})",
        default_params={"loss": "mse", "num_leaves": 31, "learning_rate": 0.05, "n_estimators": 300},
        pip_packages=["lightgbm"],
        suitable_for=["tabular", "fast", "small_data", "multi_factor"],
        performance=ModelPerformance(ic_mean=0.041, ic_std=0.075, ir=0.55,
                                     fit_seconds=12.0, benchmark="dataset://alpha158_cn"),
        tags=["推荐", "GBDT", "CPU"],
    ))
    registry.register(ModelSpec(
        model_id="xgboost", name="XGBoost", category="gbdt", status="active",
        description="XGBoost 梯度提升，与 LGBM 互补，适合集成。",
        imports=["from qlib.contrib.model.xgboost import XGBModel"],
        constructor="XGBModel(**{params})",
        default_params={"objective": "reg:squarederror", "max_depth": 6, "eta": 0.05, "n_estimators": 300},
        pip_packages=["xgboost"],
        suitable_for=["tabular", "multi_factor", "ensemble"],
        performance=ModelPerformance(ic_mean=0.038, ic_std=0.078, ir=0.49,
                                     fit_seconds=18.0, benchmark="dataset://alpha158_cn"),
        tags=["GBDT", "CPU"],
    ))
    registry.register(ModelSpec(
        model_id="catboost", name="CatBoost", category="gbdt", status="beta",
        description="CatBoost，对类别特征友好，抗过拟合较好。",
        imports=["from qlib.contrib.model.catboost_model import CatBoostModel"],
        constructor="CatBoostModel(**{params})",
        default_params={"loss_function": "RMSE", "depth": 6, "learning_rate": 0.05, "iterations": 300},
        pip_packages=["catboost"],
        suitable_for=["tabular", "multi_factor", "robust"],
        performance=ModelPerformance(ic_mean=0.036, ic_std=0.080, ir=0.45,
                                     fit_seconds=25.0, benchmark="dataset://alpha158_cn"),
        tags=["GBDT", "CPU", "新"],
    ))

    # 线性/集成：轻量、可解释
    registry.register(ModelSpec(
        model_id="linear", name="线性回归", category="linear", status="active",
        description="线性模型，可解释性强、极快，适合基线与小数据。",
        imports=["from qlib.contrib.model.linear import LinearModel"],
        constructor="LinearModel(**{params})",
        default_params={"fit_intercept": True},
        suitable_for=["fast", "small_data", "interpretable"],
        performance=ModelPerformance(ic_mean=0.022, ic_std=0.06, ir=0.37,
                                     fit_seconds=2.0, benchmark="dataset://alpha158_cn"),
        tags=["线性", "CPU", "可解释"],
    ))
    registry.register(ModelSpec(
        model_id="double_ensemble", name="双重集成", category="ensemble", status="beta",
        description="qlib DoubleEnsemble，样本加权+特征筛选，稳健但较慢。",
        imports=["from qlib.contrib.model.double_ensemble import DoubleEnsembleModel"],
        constructor="DoubleEnsembleModel(**{params})",
        default_params={},
        suitable_for=["ensemble", "robust"],
        performance=ModelPerformance(ic_mean=0.040, ic_std=0.085, ir=0.47,
                                     fit_seconds=60.0, benchmark="dataset://alpha158_cn"),
        tags=["集成", "CPU"],
    ))

    # 深度模型：需 GPU，按需加载，默认不推荐
    registry.register(ModelSpec(
        model_id="lstm", name="LSTM", category="deep_learning", status="beta",
        description="时序模型，捕捉序列依赖，需 GPU 与较大数据。",
        imports=["from qlib.contrib.model.pytorch_lstm import LSTMModel"],
        constructor="LSTMModel(**{params})",
        default_params={"hidden_size": 64, "num_layers": 2, "lr": 0.001, "n_epochs": 20},
        requires_gpu=True,
        pip_packages=["torch"],
        suitable_for=["sequence", "large_data"],
        performance=ModelPerformance(benchmark="dataset://alpha158_cn"),
        tags=["深度学习", "GPU", "序列"],
    ))
    registry.register(ModelSpec(
        model_id="transformer", name="Transformer", category="deep_learning", status="beta",
        description="时序 Transformer，注意力捕捉长程依赖，需 GPU。",
        imports=["from qlib.contrib.model.pytorch_transformer import TransformerModel"],
        constructor="TransformerModel(**{params})",
        default_params={"d_model": 64, "nhead": 4, "lr": 0.001, "n_epochs": 20},
        requires_gpu=True,
        pip_packages=["torch"],
        suitable_for=["sequence", "large_data"],
        performance=ModelPerformance(benchmark="dataset://alpha158_cn"),
        tags=["深度学习", "GPU", "序列"],
    ))
