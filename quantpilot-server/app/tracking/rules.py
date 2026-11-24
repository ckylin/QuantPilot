"""判定规则与组合构造 —— 纯函数，不碰存储、不碰网络。

这里放的三件事都有"说错话会很贵"的性质，所以全部确定性、可单测：

1. ``default_horizon`` 观察窗口：随调仓周期缩放，避免"月频策略用 20 天判死刑"；
2. ``build_targets`` 组合构造：排序 → topk → 加权 → 单票上限（封顶不归一，剩余留现金）；
3. ``evidence_level`` / ``checkpoint_text`` 证据分级与判定文案：只描述证据强度，不承诺收益。
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

FREQ_LABEL = {"day": "每天", "week": "每周", "month": "每月"}

# 观察窗口 = max(20, 3 × 调仓周期交易日)
HORIZON_BY_FREQ = {"day": 20, "week": 30, "month": 90}

EVIDENCE_TEXT = {
    "L0": "只有历史回测，还说明不了什么",
    "L1": "跑了几天，只能算体检",
    "L2": "有 20 天以上的真实记录，算初步证据",
    "L3": "跑够 60 天且和回测预期基本一致，证据较强",
}


def default_horizon(freq: str) -> int:
    """观察窗口（交易日）。至少覆盖 3 个完整调仓周期。"""
    return HORIZON_BY_FREQ.get(freq, 20)


def default_rules(freq: str, horizon_days: int | None = None) -> dict[str, Any]:
    """上线即锁定的默认判定规则；之后只读。"""
    horizon = horizon_days or default_horizon(freq)
    return {
        "horizon_days": horizon,
        "checkpoints": [7, 20, 60],
        "fail_excess": -0.08,            # 20 日跑输基准 8 个点 → 建议停
        "fail_flat_days": 10,            # 连续空仓 10 天
        "fail_stale_days": 5,            # 目标组合连续 5 天一模一样（疑似数据卡死）
        "evidence_l2_days": 20,
        "evidence_l3_days": 60,
        "deviation_tolerance": 0.05,     # 与回测预期的超额偏离容忍度
        "slippage_bps": 10.0,            # 双边滑点假设
        "min_weight_change": 0.01,       # 权重变动小于 1% 不下单（避免无意义换手）
        "max_gap_days": 3,               # 连续 3 天取不到行情 → 自动暂停
        "lot_size": 100,                 # A 股一手
    }


def estimated_verdict_date(start: date, horizon_days: int) -> date:
    """按工作日粗估"出结论日期"。

    **注意**：未扣法定节假日，只用于 UI 给个大致预期，不用于任何调度判断。
    调用方必须在响应里带上 ``note`` 说明这是估算。
    """
    remaining = max(0, horizon_days)
    cursor = start
    while remaining > 0:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            remaining -= 1
    return cursor


def is_rebalance_day(freq: str, run_date: date, last_run_date: date | None) -> bool:
    """是否该重新出信号。

    用"本周期内第一次运行"来判断，而不是硬编码"每月 1 号"：
    月初 1 号是节假日、或者中间断档补跑，都不会漏掉调仓。
    """
    if freq == "day" or last_run_date is None:
        return True
    if freq == "week":
        return run_date.isocalendar()[:2] != last_run_date.isocalendar()[:2]
    if freq == "month":
        return (run_date.year, run_date.month) != (last_run_date.year, last_run_date.month)
    return True


def build_targets(
    spec: dict[str, Any], scores: dict[str, float]
) -> list[dict[str, Any]]:
    """打分 → 排序 → topk → 权重。返回 ``[{symbol, weight, score}]``。

    权重之和通常 < 1：单票封顶后**不重新归一**，缺口留现金。
    这是刻意的——满仓会掩盖"买不满"这个真实约束。
    """
    cleaned = [
        (symbol, float(score))
        for symbol, score in scores.items()
        if score is not None and math.isfinite(float(score))
    ]
    if not cleaned:
        return []

    descending = (spec.get("signal") or {}).get("direction", "desc") != "asc"
    cleaned.sort(key=lambda item: item[1], reverse=descending)

    portfolio = spec.get("portfolio") or {}
    topk = int(portfolio.get("topk") or 20)
    picked = cleaned[:topk]
    n = len(picked)

    if portfolio.get("weighting") == "score":
        # 按排名线性加权（不直接用原始分数：分数尺度不可控，且可能为负）
        denom = n * (n + 1) / 2
        raw = [(symbol, (n - i) / denom, score) for i, (symbol, score) in enumerate(picked)]
    else:
        raw = [(symbol, 1.0 / n, score) for symbol, score in picked]

    risk = spec.get("risk") or {}
    cap_values = [
        value for value in (
            portfolio.get("max_single_weight"), risk.get("max_single_weight")
        ) if value
    ]
    cap = min(cap_values) if cap_values else None

    targets = []
    for symbol, weight, score in raw:
        if cap is not None:
            weight = min(weight, float(cap))
        targets.append({"symbol": symbol, "weight": round(weight, 6), "score": round(score, 6)})
    return targets


def target_key(targets: list[dict[str, Any]]) -> str:
    """目标组合指纹，用于检测"信号卡死"。"""
    return "|".join(f"{t['symbol']}:{t['weight']:.4f}" for t in targets)


def evidence_level(days: int, rules: dict[str, Any], deviation_ok: bool = True) -> str:
    """按**真实前向天数**定级。影子回填不计入（P1.6 尚无影子）。"""
    if days <= 0:
        return "L0"
    if days >= int(rules.get("evidence_l3_days", 60)) and deviation_ok:
        return "L3"
    if days >= int(rules.get("evidence_l2_days", 20)):
        return "L2"
    return "L1"


def checkpoint_text(day_n: int, metrics: dict[str, Any], horizon: int) -> str:
    """检查点的人话小结。只说证据强度，不说"有效"。"""
    excess = metrics.get("excess")
    excess_text = "还没有基准可比" if excess is None else f"同期超额 {_pct(excess)}"
    positions = metrics.get("avg_positions", 0)
    core = f"跑了 {day_n} 个交易日，累计 {_pct(metrics.get('cum_return', 0))}，{excess_text}"

    if day_n < horizon:
        tail = f"离 {horizon} 天的初步结论还差 {horizon - day_n} 天，现在下判断太早。"
    else:
        tail = "已经够下第一份结论了，看下面的判定。"
    # 上线头几天没建仓是正常的（信号次日才执行），不能一上来就喊"数据有问题"
    if positions < 1 and day_n >= 5:
        tail = "一直没建仓，先查是不是选股条件太严或数据没更新。"
    return f"{core}。{tail}"


def fail_reason(kind: str, value: Any, rules: dict[str, Any]) -> str:
    """失效线触发的提示。给"建议"不给"结论"，最后一步永远由用户决定。"""
    if kind == "excess":
        return (f"最近 20 天跑输基准 {_pct(abs(float(value)))}，"
                f"超过当初定的 {_pct(abs(float(rules.get('fail_excess', -0.08))))} 线。"
                "建议先停掉，看看是选股走偏还是成交受阻。")
    if kind == "flat":
        return (f"连续 {value} 天没有持仓，可能是选股条件太严，或者行情数据没更新。"
                "先别急着改参数，我建议查一下数据。")
    if kind == "stale":
        return (f"目标组合连续 {value} 天一模一样，正常策略不会这样。"
                "大概率是数据卡住了，不是策略真有这么稳。")
    return f"触发失效线（{kind}={value}）。"


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:+.2f}%"
