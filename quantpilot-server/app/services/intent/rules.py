"""规则版意图解析：离线可用、零依赖的兜底 NLU。

两个用途：
1. 没有 LLM key / LLM 输出不合法时的兜底，保证用户永远能得到一个结果；
2. 与 LLM 结果合并，补齐 LLM 漏掉的槽位（LLM 有值优先，见 ``IntentFrame.filled``）。

刻意保持"笨"：关键词 + 正则，可解释、可回归测试。
"""

from __future__ import annotations

import re

from app.services.intent.schemas import (
    REBALANCE_CN,
    UNIVERSE_CN,
    IntentFrame,
)

# 每个风格的关键词。命中即计入，不做互斥（"又便宜又稳"是合法的组合）。
STYLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "momentum": (
        "涨得猛", "涨得最", "涨幅", "追涨", "又强", "强者", "强势", "动量",
        "趋势", "新高", "领涨", "涨的", "上涨", "热门",
    ),
    "reversal": (
        "抄底", "超跌", "跌得多", "跌得最", "跌的", "下跌", "反弹",
        "反转", "回撤大", "跌下来",
    ),
    "low_volatility": (
        "稳", "波动小", "低波", "别大起大落", "平稳", "回撤小",
        "风险小", "保守", "不刺激",
    ),
    "volume": ("放量", "成交活跃", "有量", "量能", "换手", "活跃", "成交量"),
    "price_low": ("便宜", "低价", "低位", "估值低", "划算"),
    "model": (
        "机器学习", "ai", "预测", "智能", "训练", "自动学习", "让模型",
        "深度学习", "神经网络",
    ),
}

NEGATIONS = ("不要", "不用", "别", "避开", "除了")

# 当前数据不支持的诉求：必须如实告知，不许用相近的东西冒充。
UNSUPPORTED_TERMS: dict[str, str] = {
    "roe": "净资产收益率",
    "市盈率": "市盈率",
    "市净率": "市净率",
    "pe": "市盈率",
    "pb": "市净率",
    "财务": "财务指标",
    "营收": "营收",
    "利润": "利润",
    "净利润": "净利润",
    "北向": "北向资金",
    "龙虎榜": "龙虎榜",
    "新闻": "新闻舆情",
    "研报": "研报",
    "情绪": "市场情绪",
    "股息": "股息率",
    "分红": "分红",
    "涨停": "涨停板数据",
}

_TOPIC_CN = {
    "momentum": "最近涨得最猛的",
    "reversal": "最近跌得比较多、可能反弹的",
    "low_volatility": "过去一段时间最平稳的",
    "volume": "最近成交最活跃的",
    "price_low": "价格处于相对低位的",
    "model": "由模型自动学习规律来挑的",
}


def _negated(text: str, keyword: str) -> bool:
    idx = text.find(keyword)
    if idx <= 0:
        return False
    window = text[max(0, idx - 4):idx]
    return any(neg in window for neg in NEGATIONS)


def _detect_universe(text: str) -> str | None:
    if "中证1000" in text or "1000" in text:
        return "csi1000"
    if "中证500" in text or "500" in text:
        return "csi500"
    if "沪深300" in text or "300" in text:
        return "csi300"
    if any(k in text for k in ("全市场", "所有股票", "全部A股", "a股", "A股")):
        return "all"
    return None


def _detect_topk(text: str) -> int | None:
    for pattern in (r"(\d+)\s*只", r"前\s*(\d+)", r"top\s*(\d+)", r"(\d+)\s*个股票"):
        match = re.search(pattern, text)
        if match:
            value = int(match.group(1))
            if 1 <= value <= 500:
                return value
    return None


def _detect_rebalance(text: str) -> str | None:
    if any(k in text for k in ("每天", "每日", "日调", "天天")):
        return "day"
    if any(k in text for k in ("每周", "周调", "周度", "一周")):
        return "week"
    if any(k in text for k in ("每月", "月度", "月调", "一个月")):
        return "month"
    return None


def _detect_risk(text: str) -> str | None:
    if any(k in text for k in ("激进", "猛一点", "搏", "高风险")):
        return "aggressive"
    if any(k in text for k in ("稳", "保守", "小心", "别亏", "低风险")):
        return "conservative"
    return None


def detect_unsupported(text: str) -> list[str]:
    """返回当前数据做不到的诉求（中文名，去重保序）。"""
    lowered = text.lower()
    found: list[str] = []
    for term, label in UNSUPPORTED_TERMS.items():
        if term in lowered and label not in found:
            found.append(label)
    return found


def restate(frame: IntentFrame) -> str:
    """把结构化结果说回一句人话。用户只看这一句。"""
    parts: list[str] = []
    if frame.universe:
        parts.append(f"在{UNIVERSE_CN[frame.universe]}里")
    else:
        parts.append("在沪深300里")

    if frame.styles:
        topics = "、".join(_TOPIC_CN[s] for s in frame.styles).rstrip("的")
        parts.append(f"挑{topics}的 {frame.topk or 20} 只")
    else:
        parts.append(f"挑一批股票（先按 {frame.topk or 20} 只）")

    if frame.rebalance:
        parts.append(f"{REBALANCE_CN[frame.rebalance]}换一次")
    else:
        parts.append("每月换一次")
    return "，".join(parts)


def heuristic_frame(intent: str, structured: dict | None = None) -> IntentFrame:
    """关键词版解析。置信度保守给出，缺关键项时交给 Clarifier 追问。"""
    text = (intent or "").strip()
    lowered = text.lower()

    styles = [
        style
        for style, keywords in STYLE_KEYWORDS.items()
        if any(kw in lowered and not _negated(lowered, kw) for kw in keywords)
    ]
    # 只提到"稳"通常意味着低波；但"稳赚"这种话术不算
    if "稳赚" in text and "low_volatility" in styles:
        styles.remove("low_volatility")

    universe = _detect_universe(text)
    topk = _detect_topk(text)
    rebalance = _detect_rebalance(text)
    risk = _detect_risk(text)
    need_model = "model" in styles

    # 结构化参数（引导式表单）优先级最高
    for key, value in (structured or {}).items():
        if key == "universe" and value in UNIVERSE_CN:
            universe = value
        elif key == "topk" and isinstance(value, int):
            topk = value
        elif key == "rebalance" and value in REBALANCE_CN:
            rebalance = value
        elif key == "risk_level" and value in ("conservative", "balanced", "aggressive"):
            risk = value
        elif key == "styles" and isinstance(value, list):
            styles = [s for s in value if s in STYLE_KEYWORDS]

    missing: list[str] = []
    if not universe:
        missing.append("universe")
    if not styles:
        missing.append("styles")

    confidence = 0.30
    if styles:
        confidence += 0.30
    if universe:
        confidence += 0.15
    if topk or rebalance:
        confidence += 0.10
    if len(styles) == 1:
        confidence += 0.05
    confidence = round(min(confidence, 0.90), 2)

    frame = IntentFrame(
        universe=universe,
        styles=styles,
        topk=topk,
        rebalance=rebalance,
        risk_level=risk,
        need_model=need_model,
        confidence=confidence,
        missing=missing,
    )
    frame.restated = restate(frame)
    return frame
