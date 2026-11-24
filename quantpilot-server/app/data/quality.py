"""Quality gate — block bad data before it reaches a backtest.

Checks coverage, symbol validity, date sanity, missing rate and (critically)
PIT compliance. Errors block; warnings surface but don't block.
"""

from __future__ import annotations

import pandas as pd

from app.data.schemas import DataSource, QualityCheck, QualityReport

_SYMBOL_RE = __import__("re").compile(r"^(SH|SZ)\d{6}$")


def run_quality_checks(df: pd.DataFrame, expected_source: DataSource | None = None) -> QualityReport:
    checks: list[QualityCheck] = []

    # 1. 必要列
    has_symbol = "symbol" in df.columns
    has_date = "date" in df.columns
    checks.append(QualityCheck(
        name="required_columns",
        passed=has_symbol and has_date,
        detail=f"symbol={has_symbol}, date={has_date}",
    ))

    # 2. 数据量
    checks.append(QualityCheck(
        name="nonempty",
        passed=len(df) > 0,
        detail=f"{len(df)} 行",
    ))

    if has_symbol:
        # 3. 股票代码合法性
        bad = [s for s in df["symbol"].unique()[:1000] if not _SYMBOL_RE.match(str(s))]
        checks.append(QualityCheck(
            name="symbol_format",
            passed=len(bad) == 0,
            detail=f"非法代码 {len(bad)} 个: {bad[:3]}",
            severity="error" if len(bad) > 0 else "info",
        ))

    if has_date and len(df):
        # 4. 日期合法性 + 范围
        dates = pd.to_datetime(df["date"], errors="coerce")
        checks.append(QualityCheck(
            name="date_valid",
            passed=dates.notna().all(),
            detail=f"无效日期 {int(dates.isna().sum())} 条",
        ))
        if expected_source and (expected_source.coverage_start or expected_source.coverage_end):
            lo = dates.min()
            hi = dates.max()
            if expected_source.coverage_start:
                ok = lo >= pd.Timestamp(expected_source.coverage_start)
                checks.append(QualityCheck(name="coverage_start", passed=bool(ok),
                                           detail=f"实际起始 {lo.date()}",
                                           severity="warning"))
            if expected_source.coverage_end:
                ok = hi <= pd.Timestamp(expected_source.coverage_end)
                checks.append(QualityCheck(name="coverage_end", passed=bool(ok),
                                           detail=f"实际结束 {hi.date()}",
                                           severity="warning"))

    # 5. 缺失率（数据列）
    data_cols = [c for c in df.columns if c not in ("symbol", "date") and not c.startswith("_raw_")]
    if data_cols:
        missing = df[data_cols].isna().mean().mean()
        checks.append(QualityCheck(
            name="missing_rate",
            passed=missing <= 0.5,
            detail=f"平均缺失率 {missing:.1%}",
            severity="error" if missing > 0.5 else "warning",
        ))

    # 6. PIT 合规：声称 PIT 的数据源必须有可用日字段（标准化后会变 _raw_*）
    kind = str(getattr(expected_source.kind, "value", expected_source.kind)) if expected_source else ""
    if expected_source and expected_source.point_in_time and kind in ("api_external", "llm_derived"):
        pit_ok = bool(expected_source.available_date_field)
        checks.append(QualityCheck(
            name="pit_compliance",
            passed=pit_ok,
            detail=f"available_date_field={expected_source.available_date_field}",
        ))

    asset_id = expected_source.asset_id if expected_source else "unknown"
    return QualityReport(asset_id=asset_id, checks=checks)
