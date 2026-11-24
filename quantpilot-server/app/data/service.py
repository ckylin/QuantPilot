"""DataService — the single facade the rest of the app talks to.

Bundles catalog + factor store + resolver + normalization + quality into one
entry point, so callers never wire the pieces together themselves.
"""

from __future__ import annotations

import pandas as pd

from app.data.catalog import AssetCatalog, get_catalog
from app.data.factors import FactorStore, get_factor_store
from app.data.normalize import normalize_frame
from app.data.quality import run_quality_checks
from app.data.resolver import DatasetResolver
from app.data.schemas import (
    AnyAsset,
    AssetType,
    DataSource,
    DatasetDef,
    Factor,
    LineageEdge,
    QualityReport,
)


class DataService:
    def __init__(self, catalog: AssetCatalog | None = None,
                 factors: FactorStore | None = None):
        self.catalog = catalog or get_catalog()
        self.factors = factors or get_factor_store()
        self.resolver = DatasetResolver(self.catalog, self.factors)

    # ---- 资产登记 ---------------------------------------------------------

    def register(self, asset: AnyAsset, derive_from: list[str] | None = None) -> str:
        aid = self.catalog.register(asset)
        parents = list(derive_from or [])

        # 因子：继承显式来源
        if isinstance(asset, Factor):
            self.factors.register(asset, derive_from=parents)
            parents = parents or list(asset.source_refs)

        # 数据集：自动从 universe_ref + factor_refs 建立血缘
        if isinstance(asset, DatasetDef):
            parents = parents + [asset.universe_ref]
            for fref in asset.factor_refs:
                try:
                    parents.append(self.factors.resolve(fref).asset_id)
                except (KeyError, ValueError):
                    parents.append(fref)

        for src in dict.fromkeys(parents):  # 去重保序
            self.catalog.add_lineage(
                LineageEdge(from_asset=src, to_asset=aid, relation="composed"
                            if isinstance(asset, DatasetDef) else "derived")
            )
        return aid

    def discover(self, query: str, asset_type: AssetType | None = None,
                 tags: list[str] | None = None, limit: int = 20) -> list[AnyAsset]:
        """资产检索 —— LLM 生成策略前的"查目录"入口。"""
        return self.catalog.search(query, asset_type=asset_type, tags=tags, limit=limit)

    def factor_catalog_prompt(self) -> str:
        """注入 LLM prompt 的因子知识文本。"""
        return self.factors.prompt_catalog()

    # ---- 数据接入与标准化 -------------------------------------------------

    def ingest(self, df: pd.DataFrame, source: DataSource,
               available_date_col: str | None = None) -> tuple[pd.DataFrame, QualityReport]:
        """接入一份原始数据：标准化 → 质量检查 → 生成报告。

        返回标准化后的 DataFrame 与质量报告；调用方可决定是否登记为资产。
        """
        normalized = normalize_frame(
            df,
            symbol_col="symbol",
            date_col="date",
            available_date_col=available_date_col or source.available_date_field,
        )
        report = run_quality_checks(normalized, expected_source=source)
        self.catalog.set_quality(source.asset_id, report)
        return normalized, report

    # ---- 数据集解析 -------------------------------------------------------

    def resolve_dataset(self, ref: str) -> dict:
        return self.resolver.resolve(ref)

    def dataset_spec_patch(self, ref: str) -> dict:
        return self.resolver.to_strategy_spec_patch(ref)

    # ---- 血缘 -------------------------------------------------------------

    def lineage(self, asset_id: str) -> list[dict]:
        return [e.model_dump(mode="json") for e in self.catalog.lineage_of(asset_id)]

    def upstream(self, asset_id: str) -> list[str]:
        return self.catalog.upstream(asset_id)


_service: DataService | None = None


def get_data_service() -> DataService:
    global _service
    if _service is None:
        # 配置 QP_DATABASE_URL 时自动切到 PG 仓储；否则保持内存实现。
        from app.db.factories import build_data_service
        _service = build_data_service()
    return _service


def set_data_service(service: DataService) -> None:
    global _service
    _service = service
