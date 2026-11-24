"""IntentFrame — 零代码交互的语义中间层.

LLM 只负责把中文意图收敛成这个结构（分类 + 抽槽），**不写表达式、不写代码**。
表达式与 Spec 由 RecipeCompiler 确定性生成，因此理解偏差不会变成"生成失败"。

契约见 docs/design-baseline.md §3.2。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

UniverseChoice = Literal["csi300", "csi500", "csi1000", "all"]
FreqChoice = Literal["day", "week", "month"]
RiskLevel = Literal["conservative", "balanced", "aggressive"]

# 后台能力边界内的风格枚举。新增风格必须同时在 recipes.py 注册配方。
StyleTag = Literal[
    "momentum",     # 追涨 / 强者恒强
    "reversal",     # 抄底 / 超跌反弹
    "low_volatility",  # 求稳 / 波动小
    "volume",       # 看量 / 活跃
    "price_low",    # 便宜 / 低位（价格代理，非估值）
    "model",        # 让模型自己学规律
]

UNIVERSE_CN = {
    "csi300": "沪深300",
    "csi500": "中证500",
    "csi1000": "中证1000",
    "all": "全市场",
}

REBALANCE_CN = {"day": "每个交易日", "week": "每周", "month": "每月"}

RISK_CN = {"conservative": "求稳", "balanced": "均衡", "aggressive": "激进"}


class IntentFrame(BaseModel):
    """一句话意图的结构化结果。所有字段都可缺失，缺失交给 Clarifier 处理。"""

    model_config = ConfigDict(extra="forbid")

    universe: UniverseChoice | None = None
    styles: list[StyleTag] = Field(default_factory=list)
    topk: int | None = Field(default=None, ge=1, le=500)
    rebalance: FreqChoice | None = None
    risk_level: RiskLevel | None = None
    need_model: bool = False

    # 给用户看的一句人话复述（不是 JSON、不是表达式）
    restated: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    missing: list[str] = Field(default_factory=list)

    @field_validator("styles")
    @classmethod
    def _dedupe(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for item in value:
            if item not in seen:
                seen.append(item)
        return seen

    @model_validator(mode="after")
    def _model_style(self) -> IntentFrame:
        # need_model 与 styles 保持一致，避免下游两套判断
        if self.need_model and "model" not in self.styles:
            self.styles = ["model"]
        if "model" in self.styles:
            self.need_model = True
        return self

    @property
    def needs_clarification(self) -> bool:
        return not self.styles or self.confidence < 0.6

    def filled(self, other: IntentFrame) -> IntentFrame:
        """用 other（通常是规则兜底）补齐本帧的空字段，本帧已有值优先。"""
        return IntentFrame(
            universe=self.universe or other.universe,
            styles=self.styles or other.styles,
            topk=self.topk if self.topk is not None else other.topk,
            rebalance=self.rebalance or other.rebalance,
            risk_level=self.risk_level or other.risk_level,
            need_model=self.need_model or other.need_model,
            restated=self.restated or other.restated,
            confidence=max(self.confidence, other.confidence) if self.styles else other.confidence,
            missing=self.missing or other.missing,
        )
