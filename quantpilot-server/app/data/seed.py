"""Seed assets — bootstrap the catalog with qlib public data + common factors.

These are the "免费公共数据" layer. Users/operators layer proprietary assets on
top. Performance numbers are illustrative priors meant to be replaced by real
evaluation (see FactorPerformance.evaluated_on).
"""

from __future__ import annotations

from datetime import date

from app.data.schemas import (
    DataSegment,
    DataSource,
    DatasetDef,
    Factor,
    FactorPerformance,
    SourceKind,
    UniverseAsset,
)
from app.data.service import DataService


def seed_catalog(service: DataService) -> None:
    """注册预置资产。幂等：已有资产跳过。"""
    existing = {a.asset_id for a in service.catalog.list()}

    def add(asset, derive_from=None):
        if asset.asset_id not in existing:
            service.register(asset, derive_from=derive_from)

    # ---- 数据源 ----
    qlib_src = DataSource(
        asset_id="ds:qlib_cn",
        name="qlib A股公共行情",
        kind=SourceKind.QLIB_PUBLIC,
        description="日频 OHLCV，覆盖沪深全市场，2000 年至今。公共数据，无前视问题。",
        fields=["$open", "$high", "$low", "$close", "$volume", "$vwap"],
        coverage_start=date(2000, 1, 1),
        coverage_end=date(2026, 12, 31),
        point_in_time=True,
        tags=["行情", "公共", "日频"],
    )
    add(qlib_src)

    # ---- 股票池 ----
    for name, cname in [("csi300", "沪深300"), ("csi500", "中证500"), ("csi1000", "中证1000")]:
        add(UniverseAsset(
            asset_id=f"univ:{name}", name=cname, mode="builtin", builtin_name=name,
            description=f"{cname} 指数成分股", source_refs=[qlib_src.asset_id],
            tags=["宽基", "公共"],
        ), derive_from=[qlib_src.asset_id])

    # ---- 因子（预置常见因子，带示意绩效先验）----
    # (id, 名称, 表达式, 方向, IC, ICstd, 标签)
    factor_specs = [
        ("momentum_20d", "20日动量", "$close/Ref($close,20)-1", "desc", 0.041, 0.082, ["动量", "量价"]),
        ("momentum_60d", "60日动量", "$close/Ref($close,60)-1", "desc", 0.035, 0.090, ["动量", "量价"]),
        ("reversal_5d", "5日反转", "-$close/Ref($close,5)+1", "desc", 0.028, 0.075, ["反转", "量价"]),
        ("volatility_20d", "20日波动率", "Std($close/Ref($close,1)-1,20)", "asc", 0.022, 0.060, ["低波", "风险"]),
        ("volume_ratio", "量比", "$volume/Mean($volume,20)", "desc", 0.019, 0.070, ["成交量", "量价"]),
        # 注意：真估值（$pb / $pe）需要财务数据，当前数据集只有量价，
        # 因此这里用"价格相对一年均价的位置"作为低位代理，名称与标签都必须如实。
        ("low_position_1y", "一年价格低位", "Mean($close,250)/$close-1", "desc", 0.024, 0.068, ["低位", "量价"]),
    ]
    for fid, name, expr, direction, ic, icstd, tags in factor_specs:
        add(Factor(
            asset_id=f"factor:{fid}", name=name, kind="QLIB_EXPR",
            expression=expr, direction=direction,
            description=f"预置因子：{name}",
            source_refs=[qlib_src.asset_id], tags=tags,
            performance=FactorPerformance(
                ic_mean=ic, ic_std=icstd, ir=round(ic / icstd, 2),
                evaluated_on="illustrative_prior",
            ),
        ), derive_from=[qlib_src.asset_id])

    # ---- 预置数据集 ----
    add(DatasetDef(
        asset_id="dataset:alpha158_cn",
        name="Alpha158 全因子集",
        description="qlib 内置 Alpha158 因子集，适用于监督学习预测模型。",
        universe_ref="univ:csi300", factor_refs=[], handler="alpha158",
        segments=DataSegment(
            train=(date(2008, 1, 1), date(2020, 12, 31)),
            valid=(date(2021, 1, 1), date(2022, 12, 31)),
            test=(date(2023, 1, 1), date(2024, 12, 31)),
        ),
        tags=["监督学习", "公共", "Alpha158"],
    ), derive_from=["univ:csi300"])

    add(DatasetDef(
        asset_id="dataset:momentum_cn",
        name="动量因子精选集",
        description="动量+反转+量比精选因子，适合快速实验。",
        universe_ref="univ:csi300",
        factor_refs=["@factor:momentum_20d@1.0", "@factor:reversal_5d@1.0", "@factor:volume_ratio@1.0"],
        handler="custom",
        segments=DataSegment(
            train=(date(2015, 1, 1), date(2020, 12, 31)),
            test=(date(2021, 1, 1), date(2024, 12, 31)),
        ),
        tags=["动量", "精选", "轻量"],
    ), derive_from=["univ:csi300", "factor:momentum_20d", "factor:reversal_5d", "factor:volume_ratio"])
