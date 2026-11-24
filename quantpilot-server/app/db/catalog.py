"""SQL-backed AssetCatalog with the same interface as the memory catalog."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.data.catalog import AssetCatalog
from app.data.schemas import AnyAsset, AssetType, LineageEdge, QualityReport
from app.db.repositories import PgAssetRepository


class PgAssetCatalog(AssetCatalog):
    """Drop-in replacement for the in-memory catalog.

    Inherited helpers (search / typed lists / require) operate through
    ``list()`` and ``get()``, which are overridden here to hit the database.
    """

    def __init__(self, session_factory: sessionmaker[Session]):
        # Deliberately skip AssetCatalog.__init__: no in-memory dicts or JSON file.
        self.repo = PgAssetRepository(session_factory)

    def register(self, asset: AnyAsset) -> str:
        return self.repo.save(asset)

    def get(self, asset_id: str) -> AnyAsset | None:
        return self.repo.get(asset_id)

    def unregister(self, asset_id: str) -> bool:
        return self.repo.delete(asset_id)

    def list(self, asset_type: AssetType | None = None) -> list[AnyAsset]:
        return self.repo.list(asset_type)

    def set_quality(self, asset_id: str, report: QualityReport) -> None:
        self.repo.set_quality(asset_id, report)

    def quality_of(self, asset_id: str) -> QualityReport | None:
        return self.repo.quality_of(asset_id)

    def add_lineage(self, edge: LineageEdge) -> None:
        self.repo.add_lineage(edge)

    def lineage_of(self, asset_id: str) -> list[LineageEdge]:
        return self.repo.lineage_of(asset_id)

    def upstream(self, asset_id: str) -> list[str]:
        seen: set[str] = set()
        stack = [asset_id]
        while stack:
            cur = stack.pop()
            for edge in self.repo.lineage_of(cur):
                if edge.to_asset == cur and edge.from_asset not in seen:
                    seen.add(edge.from_asset)
                    stack.append(edge.from_asset)
        return sorted(seen)

    def clear(self) -> None:
        self.repo.clear()
