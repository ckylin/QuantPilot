"""风格配方：中文想法 → 因子表达式（唯一允许出现表达式的地方）。

用户永远看不到这里的表达式。新增一种"想法"= 在这里加一个配方，
不需要改 prompt、不需要改 Spec、不能让 LLM 自由发挥。

约定：``expression`` 一律写成「越大越好」，排序方向统一 desc，
这样多个配方可以直接相加合成多因子表达式。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.operators import validate_expression


@dataclass(frozen=True)
class Recipe:
    style: str
    label: str          # 给用户看的短名，2-4 字
    pitch: str          # 一句话人话解释"这是挑什么样的股票"
    expression: str     # 越大越好；model 风格为空
    spec_tag: str       # 映射到 StrategySpec.meta.tags 白名单
    caveat: str | None = None  # 代理/近似的诚实说明


RECIPES: dict[str, Recipe] = {
    "momentum": Recipe(
        style="momentum",
        label="追涨",
        pitch="最近 20 个交易日涨得最猛的",
        expression="$close/Ref($close,20)-1",
        spec_tag="momentum",
    ),
    "reversal": Recipe(
        style="reversal",
        label="抄底",
        pitch="最近 5 个交易日跌得比较多的，赌它反弹",
        expression="Ref($close,5)/$close-1",
        spec_tag="reversal",
    ),
    "low_volatility": Recipe(
        style="low_volatility",
        label="求稳",
        pitch="过去 20 个交易日涨跌幅最小、最平稳的",
        expression="-Std($close/Ref($close,1)-1,20)",
        spec_tag="low_volatility",
    ),
    "volume": Recipe(
        style="volume",
        label="看量",
        pitch="最近成交比平时活跃得多的",
        expression="$volume/Mean($volume,20)",
        spec_tag="volume",
    ),
    "price_low": Recipe(
        style="price_low",
        label="捡便宜",
        pitch="价格比过去一年均价更低的",
        expression="Mean($close,250)/$close-1",
        spec_tag="value",
        caveat="这里用的是「价格位置」近似，不是市盈率/市净率（暂未接入财务数据）",
    ),
    "model": Recipe(
        style="model",
        label="让模型学",
        pitch="让模型从 158 个量价因子里自己学规律，再挑股票",
        expression="",
        spec_tag="multi_factor",
    ),
}


def recipe_for(style: str) -> Recipe:
    if style not in RECIPES:
        raise KeyError(f"未注册的风格: {style}")
    return RECIPES[style]


def combine_expression(styles: list[str]) -> str:
    """单风格直接用；多风格用 Rank 横截面标准化后相加，避免量纲打架。"""
    recipes = [recipe_for(s) for s in styles if s != "model"]
    if not recipes:
        raise ValueError("没有可编译的表达式风格")
    expressions = [r.expression for r in recipes]
    for expr in expressions:
        errors = validate_expression(expr)
        if errors:
            raise ValueError(f"配方表达式未通过算子白名单: {expr} -> {errors}")
    if len(expressions) == 1:
        return expressions[0]
    return " + ".join(f"Rank({expr})" for expr in expressions)


def spec_tag_for(styles: list[str]) -> str:
    if len(styles) > 1 or "model" in styles:
        return "multi_factor"
    return recipe_for(styles[0]).spec_tag


def describe_styles(styles: list[str]) -> str:
    """给"我理解成了这样"卡片用的一句话描述。"""
    labels = [recipe_for(s).label for s in styles]
    return " + ".join(labels)
