"""RecipeCompiler：IntentFrame → StrategySpec（确定性，不调用 LLM）。

这是"后台把所有东西都做了"的核心：
用户只说想法，参数默认值、因子表达式、训练区间、风控阈值全部在这里补齐，
并且把"替你做了哪些决定"记录成 assumptions 交给前端展示。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.schemas import StrategySpec
from app.services.intent.recipes import (
    combine_expression,
    describe_styles,
    recipe_for,
    spec_tag_for,
)
from app.services.intent.schemas import (
    REBALANCE_CN,
    UNIVERSE_CN,
    IntentFrame,
)

DEFAULT_TOPK = 20
DEFAULT_REBALANCE = "month"
DEFAULT_RISK = "balanced"
MAX_SINGLE_WEIGHT = {"conservative": 0.05, "balanced": 0.10, "aggressive": 0.20}
BACKTEST_YEARS = 4


class FrameCompileError(Exception):
    """IntentFrame 不足以编译成策略（通常应先用 Clarifier 追问）。"""


@dataclass
class CompiledStrategy:
    spec: StrategySpec
    assumptions: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    card: dict[str, str] = field(default_factory=dict)


def _date_range(today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    try:
        start = today.replace(year=today.year - BACKTEST_YEARS)
    except ValueError:  # 2/29
        start = today.replace(year=today.year - BACKTEST_YEARS, day=28)
    return start, today


def _model_segments(start: date, end: date) -> dict[str, Any]:
    span = (end - start).days
    train_end = start + timedelta(days=int(span * 0.6))
    test_start = start + timedelta(days=int(span * 0.8))
    return {"train": (start, train_end), "test": (test_start, end)}


class FrameCompiler:
    """把语义帧编译为合法 Spec。所有默认值都必须记入 assumptions。"""

    def compile(
        self,
        frame: IntentFrame,
        *,
        intent: str = "",
        today: date | None = None,
    ) -> CompiledStrategy:
        if not frame.styles:
            raise FrameCompileError("还不知道你想挑什么样的股票")

        assumptions: list[str] = []
        caveats: list[str] = []

        universe = frame.universe or "csi300"
        if not frame.universe:
            assumptions.append(f"你没说从哪个池子选，我按{UNIVERSE_CN[universe]}")

        topk = frame.topk or DEFAULT_TOPK
        if not frame.topk:
            assumptions.append(f"你没说买多少只，我按 {topk} 只")

        rebalance = frame.rebalance or DEFAULT_REBALANCE
        if not frame.rebalance:
            assumptions.append(f"你没说多久换一次，我按{REBALANCE_CN[rebalance]}换")

        risk_level = frame.risk_level or DEFAULT_RISK
        start, end = _date_range(today)
        assumptions.append(f"回测区间用最近 {BACKTEST_YEARS} 年（{start} ~ {end}）")

        for style in frame.styles:
            recipe = recipe_for(style)
            if recipe.caveat:
                caveats.append(recipe.caveat)

        if frame.need_model:
            signal: dict[str, Any] = {
                "type": "model",
                "direction": "desc",
                "model": {
                    "type": "lgbm",
                    "features": "alpha158",
                    "segments": _model_segments(start, end),
                },
            }
        else:
            signal = {
                "type": "expression",
                "expression": combine_expression(frame.styles),
                "direction": "desc",
            }

        name = f"{UNIVERSE_CN[universe]}{describe_styles(frame.styles)}{topk}只"
        name += f"·{REBALANCE_CN[rebalance]}调" if rebalance else ""

        spec = StrategySpec.model_validate({
            "spec_version": "0.1",
            "meta": {
                "name": name[:60],
                "intent": intent or frame.restated or name,
                "tags": [spec_tag_for(frame.styles)],
            },
            "universe": {"type": "builtin", "name": universe},
            "date_range": {"start": start, "end": end},
            "rebalance": {"freq": rebalance},
            "signal": signal,
            "portfolio": {
                "selection": "topk",
                "topk": topk,
                "weighting": "equal",
                "max_single_weight": MAX_SINGLE_WEIGHT[risk_level],
            },
            "risk": {"max_single_weight": MAX_SINGLE_WEIGHT[risk_level]},
            "benchmark": "csi300",
        })

        card = {
            "标的": UNIVERSE_CN[universe],
            "挑什么": "、".join(recipe_for(s).pitch for s in frame.styles),
            "买多少": f"{topk} 只，等权买入",
            "多久换": REBALANCE_CN[rebalance],
            "回测区间": f"{start} ~ {end}",
            "风控": f"单只不超过 {MAX_SINGLE_WEIGHT[risk_level]:.0%}",
        }
        return CompiledStrategy(spec=spec, assumptions=assumptions, caveats=caveats, card=card)
