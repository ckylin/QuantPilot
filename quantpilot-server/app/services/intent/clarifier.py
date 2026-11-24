"""Clarifier：缺信息时给选择题，而不是开放式追问。

规则（见 docs/design-baseline.md §3.3）：
- 缺风格 → 必答，给 4 个选项，每个配一句人话
- 置信度低 → 让用户确认「我理解对了吗」
- 缺标的池 → 可答可不答，默认沪深300，不阻塞
- 缺 topk / 频率 / 风控 → 不问，用默认值写进 assumptions
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.intent.recipes import recipe_for
from app.services.intent.schemas import UNIVERSE_CN, IntentFrame

STYLE_CANDIDATES = ("momentum", "reversal", "low_volatility", "price_low")


@dataclass
class Question:
    key: str
    title: str
    options: list[dict[str, Any]] = field(default_factory=list)
    recommended: str | None = None
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "options": self.options,
            "recommended": self.recommended,
            "blocking": self.blocking,
        }


def _style_question() -> Question:
    options = []
    for style in STYLE_CANDIDATES:
        recipe = recipe_for(style)
        options.append({"value": style, "label": recipe.label, "hint": recipe.pitch})
    return Question(
        key="styles",
        title="你想挑什么样的股票？",
        options=options,
        recommended="momentum",
        blocking=True,
    )


def _universe_question() -> Question:
    options = [
        {"value": "csi300", "label": "沪深300", "hint": "大盘蓝筹，最常用"},
        {"value": "csi500", "label": "中证500", "hint": "中盘，波动更大"},
        {"value": "csi1000", "label": "中证1000", "hint": "小盘，弹性大"},
        {"value": "all", "label": "全市场", "hint": "所有 A 股，选股面最广"},
    ]
    return Question(
        key="universe",
        title="从哪个池子里挑？",
        options=options,
        recommended="csi300",
        blocking=False,
    )


def _confirm_question(frame: IntentFrame) -> Question:
    return Question(
        key="confirm",
        title=f"我理解你想的是：{frame.restated}。对吗？",
        options=[
            {"value": "yes", "label": "对，就这样", "hint": "直接开始回测"},
            {"value": "no", "label": "不太对", "hint": "我重新说一遍"},
        ],
        recommended="yes",
        blocking=True,
    )


def questions_for(frame: IntentFrame) -> list[Question]:
    if not frame.styles:
        return [_style_question()]

    questions: list[Question] = []
    if frame.confidence < 0.6:
        questions.append(_confirm_question(frame))
    if not frame.universe:
        questions.append(_universe_question())
    return questions[:2]


def is_ready(frame: IntentFrame) -> bool:
    return bool(frame.styles) and not any(q.blocking for q in questions_for(frame))
