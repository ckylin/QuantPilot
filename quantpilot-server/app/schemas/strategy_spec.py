"""Strategy Spec v0.1 — Pydantic models.

Single source of truth for the contract between the frontend, the LLM
generation layer and the qlib executor. Mirrors ``docs/contracts.md``.

All 9 cross-field validation rules from Spec §5 are implemented here.
"""

from __future__ import annotations

import re
import warnings
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.operators import validate_expression

UniverseName = Literal["csi300", "csi500", "csi1000", "all"]
Freq = Literal["day", "week", "month"]
Direction = Literal["desc", "asc"]
Benchmark = Literal["csi300", "csi500", "csi1000", "none"]

ALLOWED_TAGS = {
    "momentum", "reversal", "multi_factor", "sector_rotation",
    "low_volatility", "value", "quality", "volume", "custom",
}
UNIVERSE_CAPACITY = {"csi300": 300, "csi500": 500, "csi1000": 1000}
SYMBOL_RE = re.compile(r"^(SH|SZ)\d{6}$")


class _StrictModel(BaseModel):
    """Base model: reject unknown fields so LLM hallucinations surface early."""

    model_config = ConfigDict(extra="forbid")


class Meta(_StrictModel):
    name: str = Field(min_length=1, max_length=60)
    intent: str = Field(max_length=1000)
    description: str | None = Field(default=None, max_length=500)
    tags: list[str] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def _check_tags(cls, value: list[str]) -> list[str]:
        unknown = set(value) - ALLOWED_TAGS
        if unknown:
            raise ValueError(f"未知的标签: {sorted(unknown)}")
        return value


class Universe(_StrictModel):
    type: Literal["builtin", "custom"]
    name: UniverseName | None = None
    custom_symbols: list[str] | None = None

    @field_validator("custom_symbols")
    @classmethod
    def _check_symbols(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        if not 1 <= len(value) <= 1000:
            raise ValueError("custom_symbols 数量须在 1–1000 之间")
        invalid = [s for s in value if not SYMBOL_RE.match(s)]
        if invalid:
            raise ValueError(f"股票代码格式错误（应为 SH/SZ + 6 位数字）: {invalid[:3]}")
        return value


class DateSpan(_StrictModel):
    start: date
    end: date

    @model_validator(mode="after")
    def _order(self) -> DateSpan:
        if self.start > self.end:
            raise ValueError("区间 start 不得晚于 end")
        return self


class DateRange(_StrictModel):
    start: date
    end: date
    in_sample: DateSpan | None = None
    out_sample: DateSpan | None = None

    @model_validator(mode="after")
    def _check(self) -> DateRange:
        # 规则 4: 日期顺序 + 样本区间落在全区间内 + 不重叠
        if self.start > self.end:
            raise ValueError("date_range.start 不得晚于 end")
        spans: list[DateSpan] = []
        if self.in_sample is not None:
            if not (self.start <= self.in_sample.start <= self.in_sample.end <= self.end):
                raise ValueError("in_sample 必须落在 [start, end] 内")
            spans.append(self.in_sample)
        if self.out_sample is not None:
            if not (self.start <= self.out_sample.start <= self.out_sample.end <= self.end):
                raise ValueError("out_sample 必须落在 [start, end] 内")
            spans.append(self.out_sample)
        if len(spans) == 2:
            a, b = spans
            if a.start <= b.end and b.start <= a.end:
                raise ValueError("in_sample 与 out_sample 不得重叠")
        return self


class Rebalance(_StrictModel):
    freq: Freq
    weekday: int | None = Field(default=None, ge=1, le=5)
    monthday: int | None = Field(default=None, ge=1, le=28)

    @model_validator(mode="after")
    def _defaults(self) -> Rebalance:
        # weekday / monthday 缺省时按 Spec §3.5 填默认值
        if self.freq == "week" and self.weekday is None:
            self.weekday = 1
        if self.freq == "month" and self.monthday is None:
            self.monthday = 1
        return self


class ModelSegments(_StrictModel):
    """训练/验证/测试三段切分（映射 qlib DatasetH.segments）。"""

    train: tuple[date, date]
    valid: tuple[date, date] | None = None
    test: tuple[date, date]

    @model_validator(mode="after")
    def _order(self) -> ModelSegments:
        if self.train[0] > self.train[1]:
            raise ValueError("train 区间 start 不得晚于 end")
        if self.valid and self.valid[0] > self.valid[1]:
            raise ValueError("valid 区间 start 不得晚于 end")
        if self.test[0] > self.test[1]:
            raise ValueError("test 区间 start 不得晚于 end")
        spans = [("train", self.train[0], self.train[1])]
        if self.valid:
            spans.append(("valid", self.valid[0], self.valid[1]))
        spans.append(("test", self.test[0], self.test[1]))
        for (n1, s1, e1), (n2, s2, e2) in zip(spans, spans[1:]):
            if s2 <= e1:
                raise ValueError(f"{n1} 与 {n2} 区间须严格递增（不重叠）")
        return self


class ModelConfig(_StrictModel):
    """qlib 预测模型配置（一等公民，非实验性）。"""

    type: Literal["lgbm", "linear", "mlp", "xgboost", "catboost", "double_ensemble",
                  "lstm", "transformer"] = "lgbm"
    # 注册表模型 id（Model Zoo 一等引用）。缺省时回退到 type。
    # 通过它可引用任意已注册模型（含未来新增的），无需改 Spec 枚举。
    model_id: str | None = Field(default=None, pattern=r"^[a-z0-9_\-]+$")
    # 数据集处理器：Alpha158 为 qlib 内置 158 因子；custom 用 signals 自定义表达式
    features: Literal["alpha158", "custom"] = "alpha158"
    signals: list[str] | None = None  # features=custom 时的 qlib 表达式列表
    label: str | None = None          # 标签表达式，None 时 qlib 用默认 Ref($close,-2)/Ref($close,-1)-1
    segments: ModelSegments
    params: dict[str, Any] = Field(default_factory=dict)  # 模型超参，透传给 estimator
    # 数据模块衔接：引用 DataFoundry 的具名数据集（dataset://xxx）。
    # 设置后 signals/segments/label 由 DatasetResolver 在生成前注入，此处仅记录来源。
    dataset_ref: str | None = None

    @model_validator(mode="after")
    def _dataset_ref_format(self) -> ModelConfig:
        if self.dataset_ref and not self.dataset_ref.startswith(("dataset://", "dataset:")):
            raise ValueError("dataset_ref 须形如 dataset://<id> 或 dataset:<id>")
        return self

    @model_validator(mode="after")
    def _conditional(self) -> ModelConfig:
        if self.features == "custom" and not self.signals:
            raise ValueError("features=custom 时 signals 必填")
        if self.signals:
            from app.core.operators import validate_expression
            for i, expr in enumerate(self.signals):
                errors = validate_expression(expr)
                if errors:
                    raise ValueError(f"signals[{i}] 非法: {'; '.join(errors)}")
        return self


class Signal(_StrictModel):
    type: Literal["expression", "model"]
    expression: str | None = None
    direction: Direction = "desc"  # 模型信号恒为 desc；表达式可显式覆盖
    model: ModelConfig | None = None

    @model_validator(mode="after")
    def _conditional(self) -> Signal:
        if self.type == "expression" and not self.expression:
            raise ValueError("signal.type=expression 时 expression 必填")
        if self.type == "model" and not self.model:
            raise ValueError("signal.type=model 时 model 必填")
        # 模型信号本身预测的是未来收益，排序恒为 desc；asc 仅对表达式有意义
        if self.type == "model":
            self.direction = "desc"
        return self


class Portfolio(_StrictModel):
    selection: Literal["topk"]
    topk: int = Field(ge=1, le=500)
    n_drop: int | None = Field(default=None, ge=0)
    weighting: Literal["equal", "score"]
    max_single_weight: float | None = Field(default=None, gt=0, le=1)


class Filter(_StrictModel):
    field: str
    op: Literal["gt", "gte", "lt", "lte", "eq", "ne"]
    value: float


class Risk(_StrictModel):
    max_drawdown: float | None = Field(default=None, gt=0, le=1)
    max_single_weight: float | None = Field(default=None, gt=0, le=1)
    max_sector_weight: float | None = Field(default=None, gt=0, le=1)  # [实验性]


class Cost(_StrictModel):
    open_cost: float = Field(default=0.001, ge=0, lt=1)
    close_cost: float = Field(default=0.001, ge=0, lt=1)
    min_cost: float = Field(default=5.0, ge=0)


class Account(_StrictModel):
    initial_capital: float = Field(default=100_000_000, gt=0)


class StrategySpec(_StrictModel):
    spec_version: Literal["0.1"]
    meta: Meta
    universe: Universe
    date_range: DateRange
    rebalance: Rebalance
    signal: Signal
    portfolio: Portfolio
    filters: list[Filter] = Field(default_factory=list)
    risk: Risk = Field(default_factory=Risk)
    cost: Cost = Field(default_factory=Cost)
    benchmark: Benchmark = "csi300"
    account: Account = Field(default_factory=Account)

    @model_validator(mode="after")
    def _cross_checks(self) -> StrategySpec:
        u = self.universe
        p = self.portfolio

        # 规则 1: spec_version 由 Literal 保证
        # 规则 2: 条件必填
        if u.type == "builtin" and not u.name:
            raise ValueError("universe.type=builtin 时 name 必填")
        if u.type == "custom" and not u.custom_symbols:
            raise ValueError("universe.type=custom 时 custom_symbols 必填")

        # 规则 3: 股票代码格式由 Universe._check_symbols 保证
        # 规则 5: topk 合法性（含池容量）
        capacity = UNIVERSE_CAPACITY.get(u.name) if u.type == "builtin" else len(u.custom_symbols or [])
        if capacity is not None and p.topk > capacity:
            raise ValueError(f"topk={p.topk} 超过标的池容量 {capacity}")
        if p.n_drop is not None and p.n_drop > p.topk:
            raise ValueError("n_drop 不得大于 topk")
        if p.n_drop is None:
            p.n_drop = max(1, p.topk // 5)  # 默认值

        # 规则 6: 方向语义警告（不阻断）
        if "momentum" in self.meta.tags and self.signal.direction == "asc" and not (self.signal.expression or "").startswith("-"):
            warnings.warn("动量类策略通常为 direction=desc，请确认语义是否相反", stacklevel=2)

        # 规则 7: 风控一致性 —— 两处单票上限取更小值并归一
        w_port, w_risk = p.max_single_weight, self.risk.max_single_weight
        if w_port is not None and w_risk is not None and w_port != w_risk:
            stricter = min(w_port, w_risk)
            p.max_single_weight = stricter
            self.risk.max_single_weight = stricter

        # 规则 8: 表达式算子白名单
        if self.signal.type == "expression" and self.signal.expression:
            errors = validate_expression(self.signal.expression)
            if errors:
                raise ValueError(f"signal.expression 非法: {'; '.join(errors)}")

        # 模型信号：训练/测试区间须落在回测总区间内
        if self.signal.type == "model" and self.signal.model:
            seg = self.signal.model.segments
            spans_to_check = [("train", seg.train), ("test", seg.test)]
            if seg.valid:
                spans_to_check.append(("valid", seg.valid))
            for name, span in spans_to_check:
                if not (self.date_range.start <= span[0] <= span[1] <= self.date_range.end):
                    raise ValueError(f"model.segments.{name} 必须落在 date_range 内")
        for i, f in enumerate(self.filters):
            errors = validate_expression(f.field)
            if errors:
                raise ValueError(f"filters[{i}].field 非法: {'; '.join(errors)}")

        # 规则 9: 成本范围由 Cost 的 Field 约束保证
        return self

    @property
    def effective_n_drop(self) -> int:
        return self.portfolio.n_drop if self.portfolio.n_drop is not None else max(1, self.portfolio.topk // 5)

    @property
    def effective_max_single_weight(self) -> float | None:
        values = [v for v in (self.portfolio.max_single_weight, self.risk.max_single_weight) if v is not None]
        return min(values) if values else None
