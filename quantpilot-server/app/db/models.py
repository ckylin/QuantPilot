"""SQLAlchemy models for the DataFoundry backend.

The same models work on PostgreSQL (production) and SQLite (tests): we use the
portable ``JSON`` type instead of PG-specific ``JSONB`` so repository tests can
run without an external database.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AssetRow(Base):
    """One versioned data asset (datasource / universe / factor / dataset)."""

    __tablename__ = "qp_assets"

    asset_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    asset_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    owner: Mapped[str] = mapped_column(String(80), nullable=False, default="system")
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )


class QualityRow(Base):
    """Latest quality report for an asset."""

    __tablename__ = "qp_quality_reports"

    asset_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    passed: Mapped[bool] = mapped_column(nullable=False, default=True, index=True)
    report: Mapped[dict] = mapped_column(JSON, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)


class LineageRow(Base):
    """Directed edge: from_asset produced to_asset."""

    __tablename__ = "qp_lineage_edges"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    from_asset: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    to_asset: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    relation: Mapped[str] = mapped_column(String(32), nullable=False, default="derived")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)


Index("ix_qp_lineage_pair", LineageRow.from_asset, LineageRow.to_asset)


class ImportBatchRow(Base):
    """A CSV import together with its validation / review lifecycle."""

    __tablename__ = "qp_import_batches"

    batch_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    owner: Mapped[str] = mapped_column(String(80), nullable=False, default="user")
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="csv_upload")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="uploaded", index=True)
    source_asset_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    published_asset_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    row_count: Mapped[int] = mapped_column(nullable=False, default=0)
    columns: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    profile: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    preview: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    quality_report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    source_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    raw_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    normalized_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    submitter: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reviewer: Mapped[str | None] = mapped_column(String(80), nullable=True)
    review_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


Index("ix_qp_import_status_owner", ImportBatchRow.status, ImportBatchRow.owner)
