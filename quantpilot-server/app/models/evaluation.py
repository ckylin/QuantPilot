"""Benchmark evaluation contract — the objective arbiter of "good model".

A model is only promoted to `active` after passing the benchmark on a shared
dataset. Real training runs in the sandbox; this module defines the contract,
scoring and promotion rules so the pipeline is testable without qlib/GPU.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.models.registry import ModelRegistry
from app.models.schemas import ModelPerformance, ModelStatus


class ModelRunner(Protocol):
    """Sandbox adapter: train a model on a dataset and emit performance.

    Implemented by the executor layer (runs generated code in the podman
    sandbox with the shared benchmark dataset). Injected so evaluation stays
    testable with a fake runner.
    """

    def run(self, model_id: str, dataset_ref: str,
            params: dict[str, Any] | None = None) -> ModelPerformance: ...


@dataclass
class PromotionRule:
    """Threshold a model must clear to be promoted to `active`."""

    min_ic: float = 0.01
    min_ir: float = 0.25
    max_fit_seconds: float = 300.0
    require_test_segment: bool = True


DEFAULT_RULE = PromotionRule()


class Benchmark:
    def __init__(self, registry: ModelRegistry, runner: ModelRunner,
                 dataset_ref: str = "dataset://alpha158_cn",
                 rule: PromotionRule = DEFAULT_RULE):
        self.registry = registry
        self.runner = runner
        self.dataset_ref = dataset_ref
        self.rule = rule

    def evaluate(self, model_id: str,
                 params: dict[str, Any] | None = None) -> tuple[bool, ModelPerformance, list[str]]:
        """跑基准评测，返回 (是否过关, 绩效, 未通过原因)。"""
        m = self.registry.require(model_id)
        perf = self.runner.run(model_id, self.dataset_ref, params)
        perf.benchmark = self.dataset_ref
        reasons: list[str] = []

        if perf.ic_mean is None or perf.ic_mean < self.rule.min_ic:
            reasons.append(f"IC {perf.ic_mean} < {self.rule.min_ic}")
        if perf.ir is None or perf.ir < self.rule.min_ir:
            reasons.append(f"ICIR {perf.ir} < {self.rule.min_ir}")
        if perf.fit_seconds is not None and perf.fit_seconds > self.rule.max_fit_seconds:
            reasons.append(f"训练耗时 {perf.fit_seconds}s > {self.rule.max_fit_seconds}s")

        passed = not reasons
        # 回填绩效（无论是否过关都记录，便于比较与淘汰）
        m.performance = perf
        if passed and m.status == "beta":
            m.status = "active"
        elif not passed and m.status == "active":
            m.status = "deprecated"  # 回退：已上架但再评测不达标 → 下架
        return passed, perf, reasons

    def rank(self) -> list[tuple[str, float]]:
        """模型榜：按 ICIR 排序，供 H5/文档展示。"""
        scored = []
        for m in self.registry.list():
            ir = m.performance.ir or 0.0
            scored.append((m.model_id, ir))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored
