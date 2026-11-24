"""CSV import + review workflow for the DataFoundry.

Lifecycle::

    uploaded ──submit──> pending_review ──approve──> approved ──publish──> published
        ▲                      │
        └──────resubmit────────┴──reject──> rejected

Quality gates run at upload time (normalization + quality report) and again at
submit/publish time, so no asset can be published with a failing report.
"""

from __future__ import annotations

import io
import math
import re
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import pandas as pd

from app.core.config import settings
from app.data.schemas import DataSource, QualityReport, SourceKind
from app.data.service import DataService

if TYPE_CHECKING:  # avoid circular import when app.db is imported first
    from app.db.repositories import ImportBatchRepository

_ALLOWED_SUBMIT = {"uploaded", "rejected"}
_REVIEW_STATUSES = {"pending_review"}


class ImportWorkflowError(Exception):
    """Domain error with an HTTP-friendly status code."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _json_safe(value: Any) -> Any:
    """Convert pandas/numpy scalars to plain JSON values (NaN → None)."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, "item") and not isinstance(value, (list, dict, tuple)):
        try:
            return _json_safe(value.item())
        except (ValueError, AttributeError):
            pass
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:32]
    return slug or "upload"


def _read_csv(content: bytes) -> pd.DataFrame:
    try:
        return pd.read_csv(io.BytesIO(content), encoding="utf-8-sig")
    except UnicodeDecodeError:
        return pd.read_csv(io.BytesIO(content), encoding="gbk")


def _preview_records(df: pd.DataFrame, limit: int) -> list[dict[str, Any]]:
    if df.empty:
        return []
    return [_json_safe(r) for r in df.head(limit).to_dict("records")]


class ImportService:
    """Facade for the data import / review / publish workflow."""

    def __init__(self, data_service: DataService, batches: ImportBatchRepository):
        self.data = data_service
        self.batches = batches

    # ---- upload ----------------------------------------------------------

    def create_from_upload(
        self,
        *,
        filename: str,
        content: bytes,
        name: str | None = None,
        owner: str = "user",
        description: str = "",
        kind: str | SourceKind = SourceKind.CSV_UPLOAD,
        available_date_field: str | None = None,
    ) -> dict[str, Any]:
        if not content:
            raise ImportWorkflowError("上传文件为空")
        if len(content) > settings.import_max_bytes:
            raise ImportWorkflowError(
                f"文件超过上限 {settings.import_max_bytes // 1024 // 1024} MB"
            )

        try:
            raw = _read_csv(content)
        except Exception as exc:  # pandas raises several parser error types
            raise ImportWorkflowError(f"CSV 解析失败: {exc}") from exc

        display_name = (name or Path_name(filename)).strip()
        if not display_name:
            raise ImportWorkflowError("数据名称不能为空")

        source_asset_id = self._new_asset_id(display_name)
        kind_value = kind.value if isinstance(kind, SourceKind) else str(kind)
        source = DataSource(
            asset_id=source_asset_id,
            name=display_name,
            kind=kind_value,
            description=description,
            fields=list(raw.columns),
            point_in_time=True,
            available_date_field=available_date_field,
            owner=owner,
            status="draft",
        )

        try:
            normalized, report = self.data.ingest(raw, source, available_date_field)
        except ValueError as exc:
            raise ImportWorkflowError(str(exc)) from exc

        # Normalization renames the PIT column; record the effective field name.
        effective_pit = (
            f"_raw_{available_date_field}"
            if available_date_field and f"_raw_{available_date_field}" in normalized.columns
            else available_date_field
        )
        if "date" in normalized.columns:
            dates = pd.to_datetime(normalized["date"], errors="coerce").dropna()
        else:
            dates = pd.Series(dtype="datetime64[ns]")
        source = source.model_copy(update={
            "fields": [str(c) for c in normalized.columns],
            "symbols": [str(s) for s in normalized["symbol"].unique()[:5000]]
                       if "symbol" in normalized.columns else [],
            "coverage_start": dates.min().date() if not dates.empty else None,
            "coverage_end": dates.max().date() if not dates.empty else None,
            "available_date_field": effective_pit,
        })

        profile = {
            "raw_rows": int(len(raw)),
            "normalized_rows": int(len(normalized)),
            "symbol_count": int(normalized["symbol"].nunique())
                            if "symbol" in normalized.columns else 0,
            "date_min": _json_safe(dates.min()) if not dates.empty else None,
            "date_max": _json_safe(dates.max()) if not dates.empty else None,
            "null_rates": {
                str(c): float(normalized[c].isna().mean())
                for c in normalized.columns
            },
        }

        now = datetime.now()
        batch: dict[str, Any] = {
            "batch_id": f"imp:{uuid.uuid4().hex[:12]}",
            "filename": filename,
            "name": display_name,
            "owner": owner,
            "kind": kind_value,
            "status": "uploaded",
            "source_asset_id": source_asset_id,
            "published_asset_id": None,
            "row_count": int(len(normalized)),
            "columns": [str(c) for c in normalized.columns],
            "profile": _json_safe(profile),
            "preview": _preview_records(normalized, 20),
            "quality_report": report.model_dump(mode="json"),
            "source_payload": source.model_dump(mode="json"),
            "raw_bytes": content,
            "normalized_bytes": normalized.to_csv(index=False).encode("utf-8"),
            "submitter": None,
            "reviewer": None,
            "review_note": "",
            "created_at": now,
            "updated_at": now,
            "submitted_at": None,
            "reviewed_at": None,
            "published_at": None,
        }
        return self._detail(self.batches.create(batch))

    def _new_asset_id(self, display_name: str) -> str:
        # Always suffix: two batches with the same (or non-ASCII) name must not
        # collapse onto one asset id before either one is published.
        return f"ds:{_slugify(display_name)}-{uuid.uuid4().hex[:6]}"

    # ---- review ----------------------------------------------------------

    def submit(self, batch_id: str, submitter: str = "") -> dict[str, Any]:
        batch = self._require(batch_id)
        self._require_status(batch, _ALLOWED_SUBMIT, "提交审核")
        self._require_quality_passed(batch)
        updated = self.batches.update(batch_id, {
            "status": "pending_review",
            "submitter": submitter or batch.get("owner"),
            "submitted_at": datetime.now(),
            "reviewer": None,
            "reviewed_at": None,
            "review_note": "",
        })
        return self._detail(updated or batch)

    def review(self, batch_id: str, decision: str, reviewer: str = "",
               note: str = "") -> dict[str, Any]:
        batch = self._require(batch_id)
        self._require_status(batch, _REVIEW_STATUSES, "审核")
        decision = decision.lower()
        if decision not in ("approve", "reject"):
            raise ImportWorkflowError("审核结论须为 approve 或 reject")
        if decision == "approve":
            self._require_quality_passed(batch)
        patch = {
            "status": "approved" if decision == "approve" else "rejected",
            "reviewer": reviewer or "reviewer",
            "review_note": note,
            "reviewed_at": datetime.now(),
        }
        return self._detail(self.batches.update(batch_id, patch) or batch)

    def publish(self, batch_id: str) -> dict[str, Any]:
        batch = self._require(batch_id)
        self._require_status(batch, {"approved"}, "发布")
        self._require_quality_passed(batch)

        payload = dict(batch.get("source_payload") or {})
        if not payload:
            raise ImportWorkflowError("导入记录缺少数据源定义，无法发布")
        payload["status"] = "active"
        source = DataSource.model_validate(payload)
        existing = self.data.catalog.get(source.asset_id)
        if existing is not None and batch.get("published_asset_id") != source.asset_id:
            raise ImportWorkflowError(
                f"资产 ID 已被占用: {source.asset_id}", 409
            )
        self.data.catalog.register(source)
        self.data.catalog.set_quality(
            source.asset_id, QualityReport.model_validate(batch["quality_report"])
        )

        updated = self.batches.update(batch_id, {
            "status": "published",
            "published_asset_id": source.asset_id,
            "published_at": datetime.now(),
        })
        detail = self._detail(updated or batch)
        detail["asset"] = source.model_dump(mode="json")
        return detail

    # ---- read ------------------------------------------------------------

    def list_batches(self, status: str | None = None, owner: str | None = None,
                     limit: int = 100) -> list[dict[str, Any]]:
        return [self._detail(b) for b in self.batches.list(status, owner, limit)]

    def get(self, batch_id: str) -> dict[str, Any]:
        return self._detail(self._require(batch_id))

    def preview(self, batch_id: str, offset: int = 0, limit: int = 20) -> dict[str, Any]:
        batch = self._require(batch_id)
        payloads = self.batches.get_payloads(batch_id)
        rows: list[dict[str, Any]]
        if payloads and payloads[1]:
            try:
                df = pd.read_csv(io.BytesIO(payloads[1]))
                rows = _preview_records(df.iloc[int(offset):int(offset) + int(limit)], int(limit))
            except Exception:
                rows = batch.get("preview", [])
        else:
            rows = batch.get("preview", [])
        return {
            "batch_id": batch_id,
            "offset": offset,
            "limit": limit,
            "total": batch.get("row_count", 0),
            "items": rows,
        }

    # ---- helpers ---------------------------------------------------------

    def _require(self, batch_id: str) -> dict[str, Any]:
        batch = self.batches.get(batch_id)
        if batch is None:
            raise ImportWorkflowError(f"导入记录不存在: {batch_id}", 404)
        return batch

    @staticmethod
    def _require_status(batch: dict[str, Any], allowed: set[str], action: str) -> None:
        status = batch.get("status")
        if status not in allowed:
            raise ImportWorkflowError(
                f"当前状态 {status} 不允许{action}（允许: {', '.join(sorted(allowed))}）", 409
            )

    @staticmethod
    def _require_quality_passed(batch: dict[str, Any]) -> None:
        report = batch.get("quality_report") or {}
        if report.get("passed") is True:
            return
        failed = [c.get("name") for c in report.get("checks", []) if not c.get("passed")]
        raise ImportWorkflowError(
            "质量门禁未通过，禁止继续: " + ", ".join(str(f) for f in failed), 400
        )

    @staticmethod
    def _detail(batch: dict[str, Any]) -> dict[str, Any]:
        return {k: _json_safe(v) for k, v in batch.items()
                if k not in ("raw_bytes", "normalized_bytes")}


def Path_name(filename: str) -> str:
    """Strip directory components and the CSV extension for a default name."""
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    return re.sub(r"\.(csv|txt)$", "", base, flags=re.IGNORECASE)


_import_service: ImportService | None = None


def get_import_service() -> ImportService:
    global _import_service
    if _import_service is None:
        from app.data.service import get_data_service
        from app.db.factories import get_import_batch_repository
        _import_service = ImportService(get_data_service(), get_import_batch_repository())
    return _import_service


def set_import_service(service: ImportService | None) -> None:
    """Test injection point."""
    global _import_service
    _import_service = service
