# ============ GENERATED_BLOCK (START) — 由模型注册表注入 ============
# 预测模型任务：SPEC + 模型工厂（imports/constructor）均来自注册表声明。
SPEC = {{SPEC_DICT}}
{{MODEL_IMPORTS}}

def build_model():
    return {{MODEL_CONSTRUCTOR}}
# ============ GENERATED_BLOCK (END) ====================================

# ============ 固定 runner — qlib 原生 workflow，任何预测模型共用 ============
# 流程: qlib.init -> 数据处理器(Alpha158/custom) -> DatasetH(train/valid/test)
#       -> build_model().fit -> predict -> pred_score -> TopkDropoutStrategy -> backtest
# 模型相关的 import 与构造已上移到生成区，由 ModelRegistry 参数化注入。
# 面向 pyqlib 0.9.7 经典 API（contrib.data.handler / contrib.model.gbdt）。
import json
import math

import qlib
import pandas as pd
from qlib.constant import REG_CN
from qlib.data import D
from qlib.data.dataset import DatasetH
from qlib.data.dataset.handler import DataHandlerLP
from qlib.contrib.data.handler import Alpha158
from qlib.contrib.strategy import TopkDropoutStrategy
from qlib.contrib.evaluate import backtest_daily as qlib_backtest, risk_analysis


def _downsample(series, max_points=500):
    if len(series) <= max_points:
        return series
    return series.iloc[::math.ceil(len(series) / max_points)]


def _to_records(series):
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


def _build_dataset(spec):
    """按 Spec 构造 qlib DatasetH。alpha158 用内置因子；custom 用表达式构造 DataHandlerLP。"""
    model_cfg = spec["signal"]["model"]
    dr = spec["date_range"]
    seg = model_cfg["segments"]
    instruments = (
        spec["universe"]["name"]
        if spec["universe"]["type"] == "builtin"
        else spec["universe"]["custom_symbols"]
    )

    segments = {
        "train": [seg["train"][0], seg["train"][1]],
        "test": [seg["test"][0], seg["test"][1]],
    }
    if seg.get("valid"):
        segments["valid"] = [seg["valid"][0], seg["valid"][1]]

    if model_cfg.get("features", "alpha158") == "alpha158":
        handler = Alpha158(
            instruments=instruments,
            start_time=dr["start"],
            end_time=dr["end"],
            fit_start_time=seg["train"][0],
            fit_end_time=seg["train"][1],
        )
    else:
        # 自定义表达式数据集：用 QlibDataLoader 的标准 config 构造 DataHandlerLP。
        # config 格式: {"feature": [表达式列表], "label": [标签表达式]}
        signals = list(model_cfg["signals"])
        label = model_cfg.get("label") or "Ref($close,-2)/Ref($close,-1)-1"
        feature_names = ["F%d" % i for i in range(len(signals))]
        handler = DataHandlerLP(
            instruments=instruments,
            start_time=dr["start"],
            end_time=dr["end"],
            fit_start_time=seg["train"][0],
            fit_end_time=seg["train"][1],
            process_type=DataHandlerLP.PTYPE_A,
            infer_processors=[],
            learn_processors=[{"class": "DropnaLabel"}],
            data_loader={
                "class": "QlibDataLoader",
                "kwargs": {"config": {"feature": (signals, feature_names), "label": ([label], ["LABEL0"])}},
            },
        )

    return DatasetH(handler=handler, segments=segments), segments


def run():
    qlib.init(provider_uri="/data/qlib", region=REG_CN)

    spec = SPEC
    dataset, segments = _build_dataset(spec)
    model_cfg = spec["signal"]["model"]

    # ---- 训练预测模型（构造由注册表的 build_model 注入）----
    model = build_model()
    model.fit(dataset)

    # ---- 生成预测分数（模型对测试段的打分）----
    pred = model.predict(dataset)
    if isinstance(pred, pd.Series):
        pred_score = pd.DataFrame({"score": pred})
    else:
        pred_score = pd.DataFrame(pred)
        if pred_score.shape[1] >= 1:
            pred_score = pred_score.iloc[:, [0]]
            pred_score.columns = ["score"]

    # ---- qlib 原生组合回测：预测分数直接作为信号 ----
    pf = spec["portfolio"]
    strategy = TopkDropoutStrategy(
        signal=pred_score,
        topk=pf["topk"],
        n_drop=pf.get("n_drop") or max(1, pf["topk"] // 5),
    )
    cost = spec.get("cost", {})
    benchmark = spec.get("benchmark", "csi300")
    if benchmark in ("csi300", "csi500", "csi1000"):
        benchmark = {"csi300": "SH000300", "csi500": "SH000905", "csi1000": "SH000852"}[benchmark]
    # qlib 0.9.7 get_step_time 会访问 calendar[index+1]；end 落在数据日历最后一天
    # 会 IndexError。用真实交易日历把 test end 收敛到倒数第二天。
    test_start, test_end = segments["test"][0], segments["test"][1]
    try:
        _cal = D.calendar(start_time=test_start, end_time=test_end)
        if len(_cal) >= 2:
            test_end = str(_cal[-2])[:10]
    except Exception:
        pass
    # backtest_daily() 返回 (report_df, positions)，strategy.signal 已是 pred_score。
    report, positions = qlib_backtest(
        start_time=test_start,
        end_time=test_end,
        strategy=strategy,
        account=spec.get("account", {}).get("initial_capital", 100000000),
        benchmark=benchmark,
        exchange_kwargs={
            "freq": "day",
            "open_cost": cost.get("open_cost", 0.001),
            "close_cost": cost.get("close_cost", 0.001),
            "min_cost": cost.get("min_cost", 5.0),
            "deal_price": "close",
            "limit_threshold": 0.095,
        },
    )
    ret = report["return"]
    bench = report.get("bench")

    analysis = risk_analysis(ret)
    def _metric(name, default=0.0):
        try:
            return float(analysis.loc[name, "risk"])
        except (KeyError, TypeError):
            return default
    nav = (1 + ret).cumprod()
    drawdown = nav / nav.cummax() - 1
    if bench is not None and len(bench) == len(nav):
        benchmark_nav = (1 + bench).cumprod()
    else:
        benchmark_nav = pd.Series(1.0, index=nav.index)

    # ---- 样本内/外分段指标 ----
    def seg_metrics(seg_key):
        if seg_key not in segments:
            return {}
        s, e = segments[seg_key]
        part = ret[(ret.index >= s) & (ret.index <= e)]
        if len(part) == 0:
            return {}
        a = risk_analysis(part)
        def _seg(name, default=0.0):
            try:
                return float(a.loc[name, "risk"])
            except (KeyError, TypeError):
                return default
        return {
            "annual_return": _seg("annualized_return"),
            "sharpe": _seg("information_ratio"),
            "max_drawdown": _seg("max_drawdown"),
        }

    result = {
        "task_id": "{{TASK_ID}}",
        "status": "done",
        "model_type": model_cfg.get("type", "lgbm"),
        "feature_set": model_cfg.get("features", "alpha158"),
        "spec": spec,
        "metrics": {
            "annual_return": _metric("annualized_return"),
            "sharpe": _metric("information_ratio"),
            "max_drawdown": _metric("max_drawdown"),
            "win_rate": float((ret > 0).mean()),
            "turnover": float(report["turnover"].mean()) if "turnover" in report else 0.0,
            "calmar": (_metric("annualized_return") / abs(_metric("max_drawdown", 1.0)))
                      if _metric("max_drawdown", 1.0) else 0.0,
            "in_sample": seg_metrics("train"),
            "out_sample": seg_metrics("test"),
        },
        "series": {
            "nav": _to_records(_downsample(nav)),
            "drawdown": _to_records(_downsample(drawdown)),
            "benchmark_nav": _to_records(_downsample(benchmark_nav)),
        },
        "positions": [],
        "trades": [],
        "logs_ref": "{{LOGS_REF}}",
    }
    print("QUANTPLOT_RESULT:" + json.dumps(_json_safe(result), ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    run()
