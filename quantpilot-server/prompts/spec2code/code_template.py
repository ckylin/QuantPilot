# ============ GENERATED_BLOCK (START) — LLM 仅可修改本区域 ============
# 以下内容由 Stage B 生成；AST 检查器只放行此区域内的赋值语句。
SPEC = {{SPEC_DICT}}
EXPRESSION = {{EXPRESSION}}
FILTERS = {{FILTERS}}
STRATEGY_KWARGS = {{STRATEGY_KWARGS}}
# ============ GENERATED_BLOCK (END) ====================================

# ============ 固定 runner — 任何策略共用，禁止修改 =====================
import json
import math
import qlib
from qlib.constant import REG_CN
from qlib.data import D
from qlib.contrib.strategy import TopkDropoutStrategy
from qlib.contrib.evaluate import backtest_daily as qlib_backtest, risk_analysis


def _apply_filters(instruments, filters, start, end):
    """在打分前按 filters 过滤标的（多个条件取并集：满足任一即保留）。

    过滤针对每个标的的“最新一个交易日”取值判断（如最新价 > 5 剔除低价股），
    避免全历史 every-day 交集导致标的池被过度削减。
    """
    if not filters:
        # instruments 可能是 D.instruments() 返回的配置 dict（如 csi300），
        # 必须原样返回；list(dict) 会变成 ["market","filter_pipe"] 破坏配置。
        return instruments
    kept = set()
    for f in filters:
        values = D.features(instruments, [f["field"]], start, end, freq="day")
        series = values.iloc[:, 0]
        # 取每个标的最后一个交易日的取值
        latest = series.groupby(level="instrument").tail(1)
        passed = _compare(latest, f["op"], f["value"])
        kept |= set(passed[passed].index.get_level_values("instrument"))
    return sorted(kept) if kept else instruments


def _compare(series, op, value):
    return {
        "gt": series > value, "gte": series >= value,
        "lt": series < value, "lte": series <= value,
        "eq": series == value, "ne": series != value,
    }[op]


def _downsample(series, max_points=500):
    """净值/回撤序列裁剪降采样，避免大 payload。"""
    if len(series) <= max_points:
        return series
    step = math.ceil(len(series) / max_points)
    return series.iloc[::step]


def _to_records(series, key):
    return [{"date": str(idx), "value": float(val)} for idx, val in series.items()]


def _json_safe(obj):
    """递归把 NaN/Inf 清洗成 None，保证 JSON 可被前端 JSON.parse 消费。"""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


def run():
    qlib.init(provider_uri="/data/qlib", region=REG_CN)

    universe_cfg = SPEC["universe"]
    instruments = (
        D.instruments(universe_cfg["name"])
        if universe_cfg["type"] == "builtin"
        else universe_cfg["custom_symbols"]
    )
    dr = SPEC["date_range"]
    start, end = dr["start"], dr["end"]

    # qlib 0.9.7 backtest 的 get_step_time 会访问 calendar[index+1]；若 end 落在
    # 数据日历最后一天会 IndexError。用真实交易日历把 end 收敛到倒数第二天。
    try:
        _cal = D.calendar(start_time=start, end_time=end)
        if len(_cal) >= 2:
            end = str(_cal[-2])[:10]
    except Exception:
        pass

    instruments = _apply_filters(instruments, FILTERS, start, end)

    # 打分：按表达式取值，横截面排序
    scores = D.features(instruments, [EXPRESSION], start, end, freq="day").iloc[:, 0]
    ascending = STRATEGY_KWARGS["signal_direction"] == "asc"
    signal = scores.groupby(level="datetime").rank(ascending=ascending, pct=True)

    strategy = TopkDropoutStrategy(
        signal=signal,
        topk=STRATEGY_KWARGS["topk"],
        n_drop=STRATEGY_KWARGS["n_drop"],
    )
    cost = SPEC.get("cost", {})
    # backtest_daily() 是 qlib 0.9.7 的兼容封装：内部构造 SimulatorExecutor，
    # 返回 (report_df, positions)，report_df 含 return / cost / bench / turnover 列。
    benchmark = SPEC.get("benchmark", "csi300")
    if benchmark in ("csi300", "csi500", "csi1000"):
        benchmark = {"csi300": "SH000300", "csi500": "SH000905", "csi1000": "SH000852"}[benchmark]
    report, positions = qlib_backtest(
        start_time=start,
        end_time=end,
        strategy=strategy,
        account=SPEC.get("account", {}).get("initial_capital", 100000000),
        benchmark=benchmark,
        exchange_kwargs={
            "freq": "day",
            "open_cost": cost.get("open_cost", 0.001),
            "close_cost": cost.get("close_cost", 0.001),
            "min_cost": cost.get("min_cost", 5.0),
            "deal_price": "close",
        },
    )

    ret = report["return"]
    # risk_analysis 返回 DataFrame（index=指标名, column="risk"）
    analysis = risk_analysis(ret)
    def _metric(name, default=0.0):
        try:
            return float(analysis.loc[name, "risk"])
        except (KeyError, TypeError):
            return default
    annual = _metric("annualized_return")
    mdd = _metric("max_drawdown", 1.0)
    sharpe = _metric("information_ratio")  # qlib IR 即夏普式风险收益比
    nav = (1 + ret).cumprod()
    drawdown = nav / nav.cummax() - 1
    turnover_val = float(report["turnover"].mean()) if "turnover" in report else 0.0
    calmar = float(annual / abs(mdd)) if mdd else 0.0

    # ---- 标准化输出（前后端唯一契约）----
    result = {
        "task_id": "{{TASK_ID}}",
        "status": "done",
        "spec": SPEC,
        "metrics": {
            "annual_return": annual,
            "sharpe": sharpe,
            "max_drawdown": mdd,
            "win_rate": float((ret > 0).mean()),
            "turnover": turnover_val,
            "calmar": calmar,
            "in_sample": {},
            "out_sample": {},
        },
        "series": {
            "nav": _to_records(_downsample(nav), "nav"),
            "drawdown": _to_records(_downsample(drawdown), "drawdown"),
            "benchmark_nav": [],
        },
        "positions": [],
        "trades": [],
        "logs_ref": "{{LOGS_REF}}",
    }
    print("QUANTPLOT_RESULT:" + json.dumps(_json_safe(result), ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    run()
