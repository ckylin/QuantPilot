"""Phase 0 验收样例的 10 条 Spec（对应 docs/DEPLOYMENT.md §7.2）。

这些是每条样例的「标准答案 Spec」，用于：
  1. 本机验证：Spec 校验 + 代码生成 + AST 检查（不需要 qlib/podman）
  2. 容器自测：由 sandbox/run_acceptance.ps1 驱动，在沙箱里执行真实回测

注意：这里直接给出 Spec 是 Phase 0 的"离线基线"做法——先把执行链路验证通，
LLM 从意图生成 Spec 的质量在接真实模型后再测。验收文档里的"关键字段匹配"
在这套基线 Spec 上天然满足，重点验证的是"Spec → 代码 → 沙箱执行 → 标准化结果"。
"""

from __future__ import annotations

from typing import Any

# 每条样例: id, 意图, Spec dict
CASES: list[dict[str, Any]] = [
    {
        "id": "C01",
        "intent": "沪深300里动量最强的20只股票，每月调仓",
        "spec": {
            "spec_version": "0.1",
            "meta": {"name": "沪深300动量Top20", "intent": "沪深300里动量最强的20只股票，每月调仓", "tags": ["momentum"]},
            "universe": {"type": "builtin", "name": "csi300"},
            "date_range": {"start": "2020-01-01", "end": "2024-12-31"},
            "rebalance": {"freq": "month", "monthday": 1},
            "signal": {"type": "expression", "expression": "$close / Ref($close, 20) - 1", "direction": "desc"},
            "portfolio": {"selection": "topk", "topk": 20, "weighting": "equal"},
            "benchmark": "csi300",
        },
    },
    {
        "id": "C02",
        "intent": "中证500里最近跌得最狠的30只股票，做超跌反弹，每周调仓",
        "spec": {
            "spec_version": "0.1",
            "meta": {"name": "中证500超跌反弹Top30", "intent": "中证500里最近跌得最狠的30只股票，做超跌反弹，每周调仓", "tags": ["reversal"]},
            "universe": {"type": "builtin", "name": "csi500"},
            "date_range": {"start": "2020-01-01", "end": "2024-12-31"},
            "rebalance": {"freq": "week", "weekday": 1},
            "signal": {"type": "expression", "expression": "$close / Ref($close, 10) - 1", "direction": "asc"},
            "portfolio": {"selection": "topk", "topk": 30, "weighting": "equal"},
            "benchmark": "csi500",
        },
    },
    {
        "id": "C03",
        "intent": "在沪深300里选高动量而且低波动的30只股票，按月调仓",
        "spec": {
            "spec_version": "0.1",
            "meta": {"name": "沪深300动量低波Top30", "intent": "在沪深300里选高动量而且低波动的30只股票，按月调仓", "tags": ["multi_factor", "momentum"]},
            "universe": {"type": "builtin", "name": "csi300"},
            "date_range": {"start": "2020-01-01", "end": "2024-12-31"},
            "rebalance": {"freq": "month", "monthday": 1},
            "signal": {"type": "expression", "expression": "Rank($close/Ref($close,20)-1) + Rank(-Std($close,20)/Mean($close,20))", "direction": "desc"},
            "portfolio": {"selection": "topk", "topk": 30, "weighting": "equal"},
            "benchmark": "csi300",
        },
    },
    {
        "id": "C04",
        "intent": "中证500里面波动最小的20只股票，每月调仓",
        "spec": {
            "spec_version": "0.1",
            "meta": {"name": "中证500低波Top20", "intent": "中证500里面波动最小的20只股票，每月调仓", "tags": ["low_volatility"]},
            "universe": {"type": "builtin", "name": "csi500"},
            "date_range": {"start": "2020-01-01", "end": "2024-12-31"},
            "rebalance": {"freq": "month", "monthday": 1},
            "signal": {"type": "expression", "expression": "Std($close, 20) / Mean($close, 20)", "direction": "asc"},
            "portfolio": {"selection": "topk", "topk": 20, "weighting": "equal"},
            "benchmark": "csi500",
        },
    },
    {
        "id": "C05",
        "intent": "沪深300里最近成交量明显放大的20只股票，每周调仓",
        "spec": {
            "spec_version": "0.1",
            "meta": {"name": "沪深300放量Top20", "intent": "沪深300里最近成交量明显放大的20只股票，每周调仓", "tags": ["volume"]},
            "universe": {"type": "builtin", "name": "csi300"},
            "date_range": {"start": "2020-01-01", "end": "2024-12-31"},
            "rebalance": {"freq": "week", "weekday": 1},
            "signal": {"type": "expression", "expression": "$volume / Mean($volume, 20)", "direction": "desc"},
            "portfolio": {"selection": "topk", "topk": 20, "weighting": "equal"},
            "benchmark": "csi300",
        },
    },
    {
        "id": "C06",
        "intent": "在贵州茅台、五粮液、中国平安、招商银行这4只股票里，选动量最强的2只，每月调仓",
        "spec": {
            "spec_version": "0.1",
            "meta": {"name": "自定义4只动量Top2", "intent": "在贵州茅台、五粮液、中国平安、招商银行这4只股票里，选动量最强的2只，每月调仓", "tags": ["custom", "momentum"]},
            "universe": {"type": "custom", "custom_symbols": ["SH600519", "SZ000858", "SH601318", "SH600036"]},
            "date_range": {"start": "2020-01-01", "end": "2024-12-31"},
            "rebalance": {"freq": "month", "monthday": 1},
            "signal": {"type": "expression", "expression": "$close / Ref($close, 20) - 1", "direction": "desc"},
            "portfolio": {"selection": "topk", "topk": 2, "weighting": "equal"},
            "benchmark": "csi300",
        },
    },
    {
        "id": "C07",
        "intent": "沪深300里动量最强的20只，每月调仓，要求最大回撤不超过20%，单只股票仓位不超过5%",
        "spec": {
            "spec_version": "0.1",
            "meta": {"name": "沪深300动量Top20风控版", "intent": "沪深300里动量最强的20只，每月调仓，最大回撤不超过20%，单票不超过5%", "tags": ["momentum"]},
            "universe": {"type": "builtin", "name": "csi300"},
            "date_range": {"start": "2020-01-01", "end": "2024-12-31"},
            "rebalance": {"freq": "month", "monthday": 1},
            "signal": {"type": "expression", "expression": "$close / Ref($close, 20) - 1", "direction": "desc"},
            "portfolio": {"selection": "topk", "topk": 20, "weighting": "equal", "max_single_weight": 0.05},
            "risk": {"max_drawdown": 0.20, "max_single_weight": 0.05},
            "benchmark": "csi300",
        },
    },
    {
        "id": "C08",
        "intent": "中证500低波动策略，选15只，按周调仓，交易成本按双边千分之一算，2020到2024年回测并分样本内外",
        "spec": {
            "spec_version": "0.1",
            "meta": {"name": "中证500低波Top15样本外", "intent": "中证500低波动策略，选15只，按周调仓，双边千分之一，样本内外", "tags": ["low_volatility"]},
            "universe": {"type": "builtin", "name": "csi500"},
            "date_range": {
                "start": "2020-01-01", "end": "2024-12-31",
                "in_sample": {"start": "2020-01-01", "end": "2022-12-31"},
                "out_sample": {"start": "2023-01-01", "end": "2024-12-31"},
            },
            "rebalance": {"freq": "week", "weekday": 1},
            "signal": {"type": "expression", "expression": "Std($close, 20) / Mean($close, 20)", "direction": "asc"},
            "portfolio": {"selection": "topk", "topk": 15, "weighting": "equal"},
            "cost": {"open_cost": 0.001, "close_cost": 0.001, "min_cost": 5.0},
            "benchmark": "csi500",
        },
    },
    {
        "id": "C09",
        "intent": "做一个沪深300的行业轮动策略，追踪近期强势行业的龙头股，选20只，每月调仓",
        "spec": {
            "spec_version": "0.1",
            "meta": {"name": "沪深300强度轮动Top20", "intent": "沪深300行业轮动近似，追踪强势龙头，选20只，每月调仓", "tags": ["sector_rotation"]},
            "universe": {"type": "builtin", "name": "csi300"},
            "date_range": {"start": "2020-01-01", "end": "2024-12-31"},
            "rebalance": {"freq": "month", "monthday": 1},
            "signal": {"type": "expression", "expression": "Rank($close/Ref($close,60)-1) + Rank($volume/Mean($volume,20))", "direction": "desc"},
            "portfolio": {"selection": "topk", "topk": 20, "weighting": "equal"},
            "benchmark": "csi300",
        },
    },
    {
        "id": "C10",
        "intent": "我想在沪深300里找那种最近走势强、但成交量别太小的股票，选30只，每两周调一次仓",
        "spec": {
            "spec_version": "0.1",
            "meta": {"name": "沪深300动量放量Top30", "intent": "沪深300里走势强且成交量不太小的股票，选30只，每两周调仓（降级为每周）", "tags": ["momentum"]},
            "universe": {"type": "builtin", "name": "csi300"},
            "date_range": {"start": "2020-01-01", "end": "2024-12-31"},
            "rebalance": {"freq": "week", "weekday": 1},
            "signal": {"type": "expression", "expression": "$close / Ref($close, 20) - 1", "direction": "desc"},
            "portfolio": {"selection": "topk", "topk": 30, "weighting": "equal"},
            "filters": [{"field": "$volume", "op": "gt", "value": 100000}],
            "benchmark": "csi300",
        },
    },
]
