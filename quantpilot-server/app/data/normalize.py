"""Normalization pipeline — align symbols, dates and enforce Point-in-Time.

The critical piece here is ``pit_align``: fundamental/alt data must be stamped
with the date it became *known* (announcement date), not the report period.
Using report period directly leaks future information into backtests.
"""

from __future__ import annotations

import pandas as pd

_SYMBOL_RE = __import__("re").compile(r"^(SH|SZ)\d{6}$")


def normalize_symbol(s: str) -> str:
    """统一股票代码为 SHxxxxxx / SZxxxxxx。容忍 600000.SH / sz000001 等写法。"""
    s = str(s).strip().upper()
    if "." in s:  # 600000.SH -> SH600000
        num, _, suffix = s.partition(".")
        if suffix in ("SH", "SZ") and num.isdigit():
            return f"{suffix}{num}"
    if s.startswith(("SH", "SZ")):
        return s
    if s.isdigit() and len(s) == 6:  # 默认按 A 股推断交易所
        return ("SH" if s[0] in "56" else "SZ") + s
    return s


def normalize_frame(df: pd.DataFrame, symbol_col: str = "symbol",
                    date_col: str = "date",
                    available_date_col: str | None = None) -> pd.DataFrame:
    """标准化工序：symbol 统一 → 日期解析 → PIT 对齐 → 去重排序。"""
    out = df.copy()
    if symbol_col not in out.columns or date_col not in out.columns:
        raise ValueError(f"原始数据须含 {symbol_col} 与 {date_col} 列")

    out[symbol_col] = out[symbol_col].map(normalize_symbol)
    out[date_col] = pd.to_datetime(out[date_col], errors="coerce")

    if available_date_col and available_date_col in out.columns:
        out = pit_align(out, date_col, available_date_col)

    out = out.dropna(subset=[symbol_col, date_col])
    out = out.sort_values([symbol_col, date_col]).drop_duplicates(
        subset=[symbol_col, date_col], keep="last"
    )
    return out.reset_index(drop=True)


def pit_align(df: pd.DataFrame, date_col: str,
              available_date_col: str) -> pd.DataFrame:
    """PIT 对齐：把"报告期日期"替换为"可交易生效日"。

    规则：某条记录在 available_date 公布后，从下一个交易日起对应当日数据。
    这杜绝了用尚未公布的财务数据回测的前视偏差。实现上把 date 设为
    max(report_date, available_date)，即数据最早生效时点。
    """
    out = df.copy()
    avail = pd.to_datetime(out[available_date_col], errors="coerce")
    report = pd.to_datetime(out[date_col], errors="coerce")
    # 生效日 = 报告期与公布日中较晚者（公布之前的日期不该看到该数据）
    effective = report.where(report >= avail, avail)
    out[date_col] = effective
    out = out.rename(columns={available_date_col: f"_raw_{available_date_col}"})
    return out


def winsorize(series: pd.Series, lower: float = 0.01, upper: float = 0.99) -> pd.Series:
    """去极值：分位数缩尾，避免异常值主导因子。"""
    lo, hi = series.quantile(lower), series.quantile(upper)
    return series.clip(lo, hi)
