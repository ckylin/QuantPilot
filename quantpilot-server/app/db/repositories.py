"""Storage repositories for data assets and the import/review workflow.

``Pg*`` implementations are used when QP_DATABASE_URL is configured; the
in-memory import repository keeps local/offline runs and tests dependency-free.
"""

from __future__ import annotations

import copy
import threading
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.data.catalog import asset_type_of
from app.data.schemas import (
    AnyAsset,
    AssetType,
    DataSource,
    DatasetDef,
    Factor,
    LineageEdge,
    QualityReport,
    UniverseAsset,
)
from app.db.models import (
    AssetRow,
    ImportBatchRow,
    LineageRow,
    QualityRow,
)

_ASSET_FACTORIES = {
    "datasource": DataSource,
    "universe": UniverseAsset,
    "factor": Factor,
    "dataset": DatasetDef,
}


class AssetRepository(Protocol):
    def save(self, asset: AnyAsset) -> str: ...
    def get(self, asset_id: str) -> AnyAsset | None: ...
    def delete(self, asset_id: str) -> bool: ...
    def list(self, asset_type: AssetType | None = None) -> list[AnyAsset]: ...
    def set_quality(self, asset_id: str, report: QualityReport) -> None: ...
    def quality_of(self, asset_id: str) -> QualityReport | None: ...
    def add_lineage(self, edge: LineageEdge) -> None: ...
    def lineage_of(self, asset_id: str) -> list[LineageEdge]: ...
    def clear(self) -> None: ...


class ImportBatchRepository(Protocol):
    def create(self, batch: dict[str, Any]) -> dict[str, Any]: ...
    def get(self, batch_id: str) -> dict[str, Any] | None: ...
    def update(self, batch_id: str, patch: dict[str, Any]) -> dict[str, Any] | None: ...
    def list(self, status: str | None = None, owner: str | None = None,
             limit: int = 100) -> list[dict[str, Any]]: ...
    def get_payloads(self, batch_id: str) -> tuple[bytes, bytes] | None: ...
    def delete(self, batch_id: str) -> bool: ...


# ---------------------------------------------------------------------------
# SQL implementations
# ---------------------------------------------------------------------------

def _row_to_asset(row: AssetRow) -> AnyAsset | None:
    factory = _ASSET_FACTORIES.get(row.asset_type)
    if factory is None:
        return None
    return factory.model_validate(row.payload)


class PgAssetRepository:
    """PostgreSQL-backed asset store (also works with SQLite in tests)."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self._sf = session_factory

    def save(self, asset: AnyAsset) -> str:
        with self._sf() as s:
            row = s.get(AssetRow, asset.asset_id)
            values = {
                "asset_type": asset_type_of(asset.asset_id) or "unknown",
                "name": asset.name,
                "status": getattr(asset, "status", "active"),
                "owner": getattr(asset, "owner", "system"),
                "version": getattr(asset, "version", "1.0"),
                "payload": asset.model_dump(mode="json"),
            }
            if row is None:
                s.add(AssetRow(asset_id=asset.asset_id, **values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            s.commit()
        return asset.asset_id

    def get(self, asset_id: str) -> AnyAsset | None:
        with self._sf() as s:
            row = s.get(AssetRow, asset_id)
            return _row_to_asset(row) if row else None

    def delete(self, asset_id: str) -> bool:
        with self._sf() as s:
            row = s.get(AssetRow, asset_id)
            if row is None:
                return False
            s.delete(row)
            s.execute(QualityRow.__table__.delete().where(QualityRow.asset_id == asset_id))
            s.execute(LineageRow.__table__.delete().where(
                (LineageRow.from_asset == asset_id) | (LineageRow.to_asset == asset_id)
            ))
            s.commit()
            return True

    def list(self, asset_type: AssetType | None = None) -> list[AnyAsset]:
        with self._sf() as s:
            stmt = select(AssetRow).order_by(AssetRow.asset_id)
            if asset_type:
                stmt = stmt.where(AssetRow.asset_type == asset_type)
            rows = s.scalars(stmt).all()
            assets = [_row_to_asset(r) for r in rows]
            return [a for a in assets if a is not None]

    def set_quality(self, asset_id: str, report: QualityReport) -> None:
        with self._sf() as s:
            row = s.get(QualityRow, asset_id)
            values = {"passed": report.passed, "report": report.model_dump(mode="json"),
                      "checked_at": report.checked_at}
            if row is None:
                s.add(QualityRow(asset_id=asset_id, **values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            s.commit()

    def quality_of(self, asset_id: str) -> QualityReport | None:
        with self._sf() as s:
            row = s.get(QualityRow, asset_id)
            return QualityReport.model_validate(row.report) if row else None

    def add_lineage(self, edge: LineageEdge) -> None:
        with self._sf() as s:
            s.add(LineageRow(
                from_asset=edge.from_asset, to_asset=edge.to_asset,
                relation=edge.relation, note=edge.note, created_at=edge.created_at,
            ))
            s.commit()

    def lineage_of(self, asset_id: str) -> list[LineageEdge]:
        with self._sf() as s:
            rows = s.scalars(select(LineageRow).where(
                (LineageRow.from_asset == asset_id) | (LineageRow.to_asset == asset_id)
            )).all()
            return [LineageEdge(
                from_asset=r.from_asset, to_asset=r.to_asset,
                relation=r.relation, note=r.note, created_at=r.created_at,
            ) for r in rows]

    def clear(self) -> None:
        with self._sf() as s:
            for model in (LineageRow, QualityRow, AssetRow, ImportBatchRow):
                s.execute(model.__table__.delete())
            s.commit()


class PgImportBatchRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._sf = session_factory

    @staticmethod
    def _to_dict(row: ImportBatchRow) -> dict[str, Any]:
        return {
            "batch_id": row.batch_id,
            "filename": row.filename,
            "name": row.name,
            "owner": row.owner,
            "kind": row.kind,
            "status": row.status,
            "source_asset_id": row.source_asset_id,
            "published_asset_id": row.published_asset_id,
            "row_count": row.row_count,
            "columns": row.columns,
            "profile": row.profile,
            "preview": row.preview,
            "quality_report": row.quality_report,
            "source_payload": row.source_payload,
            "submitter": row.submitter,
            "reviewer": row.reviewer,
            "review_note": row.review_note,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "submitted_at": row.submitted_at,
            "reviewed_at": row.reviewed_at,
            "published_at": row.published_at,
        }

    def create(self, batch: dict[str, Any]) -> dict[str, Any]:
        row = ImportBatchRow(
            batch_id=batch["batch_id"],
            filename=batch["filename"],
            name=batch["name"],
            owner=batch.get("owner", "user"),
            kind=batch.get("kind", "csv_upload"),
            status=batch.get("status", "uploaded"),
            source_asset_id=batch["source_asset_id"],
            published_asset_id=batch.get("published_asset_id"),
            row_count=batch.get("row_count", 0),
            columns=batch.get("columns", []),
            profile=batch.get("profile", {}),
            preview=batch.get("preview", []),
            quality_report=batch.get("quality_report", {}),
            source_payload=batch.get("source_payload", {}),
            raw_bytes=batch.get("raw_bytes", b""),
            normalized_bytes=batch.get("normalized_bytes", b""),
            submitter=batch.get("submitter"),
            created_at=batch.get("created_at") or datetime.now(),
            submitted_at=batch.get("submitted_at"),
            reviewed_at=batch.get("reviewed_at"),
            published_at=batch.get("published_at"),
        )
        with self._sf() as s:
            s.add(row)
            s.commit()
            s.refresh(row)
            return self._to_dict(row)

    def get(self, batch_id: str) -> dict[str, Any] | None:
        with self._sf() as s:
            row = s.get(ImportBatchRow, batch_id)
            return self._to_dict(row) if row else None

    def update(self, batch_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        with self._sf() as s:
            row = s.get(ImportBatchRow, batch_id)
            if row is None:
                return None
            for key, value in patch.items():
                if hasattr(row, key):
                    setattr(row, key, value)
            s.commit()
            s.refresh(row)
            return self._to_dict(row)

    def list(self, status: str | None = None, owner: str | None = None,
             limit: int = 100) -> list[dict[str, Any]]:
        with self._sf() as s:
            stmt = select(ImportBatchRow).order_by(ImportBatchRow.created_at.desc())
            if status:
                stmt = stmt.where(ImportBatchRow.status == status)
            if owner:
                stmt = stmt.where(ImportBatchRow.owner == owner)
            rows = s.scalars(stmt.limit(limit)).all()
            return [self._to_dict(r) for r in rows]

    def get_payloads(self, batch_id: str) -> tuple[bytes, bytes] | None:
        with self._sf() as s:
            row = s.get(ImportBatchRow, batch_id)
            return (row.raw_bytes, row.normalized_bytes) if row else None

    def delete(self, batch_id: str) -> bool:
        with self._sf() as s:
            row = s.get(ImportBatchRow, batch_id)
            if row is None:
                return False
            s.delete(row)
            s.commit()
            return True


# ---------------------------------------------------------------------------
# In-memory import repository (offline / tests)
# ---------------------------------------------------------------------------

class MemoryImportBatchRepository:
    def __init__(self):
        self._data: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def create(self, batch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            item = copy.deepcopy(batch)
            self._data[item["batch_id"]] = item
            return self._detail(item)

    def get(self, batch_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._data.get(batch_id)
            return self._detail(item) if item else None

    def update(self, batch_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            item = self._data.get(batch_id)
            if item is None:
                return None
            item.update(copy.deepcopy(patch))
            return self._detail(item)

    def list(self, status: str | None = None, owner: str | None = None,
             limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            items = sorted(self._data.values(),
                           key=lambda b: str(b.get("created_at", "")), reverse=True)
            if status:
                items = [b for b in items if b.get("status") == status]
            if owner:
                items = [b for b in items if b.get("owner") == owner]
            return [self._detail(b) for b in items[:limit]]

    def get_payloads(self, batch_id: str) -> tuple[bytes, bytes] | None:
        with self._lock:
            item = self._data.get(batch_id)
            return (item.get("raw_bytes", b""), item.get("normalized_bytes", b"")) if item else None

    def delete(self, batch_id: str) -> bool:
        with self._lock:
            return self._data.pop(batch_id, None) is not None

    @staticmethod
    def _detail(item: dict[str, Any]) -> dict[str, Any]:
        return {k: copy.deepcopy(v) for k, v in item.items()
                if k not in ("raw_bytes", "normalized_bytes")}
