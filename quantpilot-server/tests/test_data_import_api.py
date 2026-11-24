"""Tests for the DataFoundry import/review workflow and PG-backed repositories."""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import create_app
from app.api import deps as api_deps
from app.data.catalog import AssetCatalog
from app.data.factors import FactorStore
from app.data.imports import ImportService, ImportWorkflowError, get_import_service, set_import_service
from app.data.schemas import DataSource, LineageEdge, QualityCheck, QualityReport, SourceKind
from app.data.service import DataService, get_data_service, set_data_service
from app.db.catalog import PgAssetCatalog
from app.db.repositories import (
    MemoryImportBatchRepository,
    PgAssetRepository,
    PgImportBatchRepository,
)
from app.db.session import configure_database, get_session_factory, init_db, reset_database
from app.orchestration import TaskService
from app.services.executor.sandbox import RESULT_MARKER, SandboxExecutor
from app.services.llm.provider import OfflineProvider

GOOD_CSV = (
    "symbol,date,close\n"
    "600000.SH,2021-01-01,10.0\n"
    "000001,2021-01-02,11.0\n"
).encode("utf-8")

BAD_CSV = (
    "symbol,date,close\n"
    "BAD,2021-01-01,10.0\n"
).encode("utf-8")


def _memory_service() -> ImportService:
    cat = AssetCatalog()
    factors = FactorStore(cat)
    data = DataService(cat, factors)
    return ImportService(data, MemoryImportBatchRepository())


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

def test_upload_submit_review_publish_flow():
    svc = _memory_service()
    batch = svc.create_from_upload(filename="prices.csv", content=GOOD_CSV,
                                   name="测试行情", owner="alice")
    assert batch["status"] == "uploaded"
    assert batch["quality_report"]["passed"] is True
    assert batch["row_count"] == 2
    assert batch["preview"][0]["symbol"] == "SH600000"
    assert "raw_bytes" not in batch

    preview = svc.preview(batch["batch_id"], limit=1)
    assert preview["total"] == 2
    assert len(preview["items"]) == 1

    submitted = svc.submit(batch["batch_id"], submitter="alice")
    assert submitted["status"] == "pending_review"
    with pytest.raises(ImportWorkflowError) as exc:
        svc.submit(batch["batch_id"])
    assert exc.value.status_code == 409

    approved = svc.review(batch["batch_id"], "approve", reviewer="bob")
    assert approved["status"] == "approved"
    published = svc.publish(batch["batch_id"])
    assert published["status"] == "published"
    assert published["published_asset_id"].startswith("ds:")
    asset = svc.data.catalog.get(published["published_asset_id"])
    assert isinstance(asset, DataSource)
    assert asset.status == "active"
    assert svc.data.catalog.quality_of(asset.asset_id) is not None


def test_quality_gate_blocks_submit():
    svc = _memory_service()
    batch = svc.create_from_upload(filename="bad.csv", content=BAD_CSV, name="坏数据")
    assert batch["quality_report"]["passed"] is False
    with pytest.raises(ImportWorkflowError) as exc:
        svc.submit(batch["batch_id"])
    assert exc.value.status_code == 400
    assert "symbol_format" in exc.value.message


def test_reject_allows_resubmit_but_not_publish():
    svc = _memory_service()
    batch = svc.create_from_upload(filename="prices.csv", content=GOOD_CSV, name="行情")
    svc.submit(batch["batch_id"])
    rejected = svc.review(batch["batch_id"], "reject", reviewer="bob", note="字段不足")
    assert rejected["status"] == "rejected"
    with pytest.raises(ImportWorkflowError):
        svc.publish(batch["batch_id"])
    assert svc.submit(batch["batch_id"])["status"] == "pending_review"


def test_missing_batch_returns_404():
    with pytest.raises(ImportWorkflowError) as exc:
        _memory_service().get("imp:missing")
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@pytest.fixture()
def api_client():
    """Fresh in-memory API app with an isolated import service."""
    svc = TaskService(
        provider=OfflineProvider(),
        executor=SandboxExecutor(runner="fake", fake_runner=lambda *_: (RESULT_MARKER + "{}", "", 0, False)),
    )
    api_deps.set_task_service(svc)
    data_platform = _memory_service()
    set_data_service(data_platform.data)
    set_import_service(data_platform)
    yield TestClient(create_app())


def test_api_import_review_publish(api_client):
    r = api_client.post("/api/v1/data/imports",
                        files={"file": ("prices.csv", GOOD_CSV, "text/csv")},
                        data={"name": "API行情", "owner": "alice"})
    assert r.status_code == 201, r.text
    batch = r.json()
    bid = batch["batch_id"]
    assert batch["status"] == "uploaded"

    assert api_client.get("/api/v1/data/imports").json()["total"] == 1
    assert api_client.get(f"/api/v1/data/imports/{bid}").json()["batch_id"] == bid
    assert api_client.get(f"/api/v1/data/imports/{bid}/preview",
                          params={"limit": 1}).json()["total"] == 2

    assert api_client.post(f"/api/v1/data/imports/{bid}/submit",
                           json={"submitter": "alice"}).status_code == 200
    r = api_client.post(f"/api/v1/data/imports/{bid}/review",
                        json={"decision": "approve", "reviewer": "bob"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    r = api_client.post(f"/api/v1/data/imports/{bid}/publish")
    assert r.status_code == 200 and r.json()["status"] == "published"


def test_api_rejects_bad_status_transition(api_client):
    bid = api_client.post("/api/v1/data/imports",
                          files={"file": ("prices.csv", GOOD_CSV, "text/csv")},
                          data={"name": "顺序测试"}).json()["batch_id"]
    r = api_client.post(f"/api/v1/data/imports/{bid}/review",
                        json={"decision": "approve"})
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# SQL repositories (SQLite stands in for PostgreSQL)
# ---------------------------------------------------------------------------

@pytest.fixture()
def sql_session_factory():
    engine = configure_database("sqlite://")
    init_db(engine)
    yield get_session_factory()
    reset_database()


def test_pg_asset_repository_roundtrip(sql_session_factory):
    repo = PgAssetRepository(sql_session_factory)
    source = DataSource(asset_id="ds:pg-test", name="PG测试", kind=SourceKind.CSV_UPLOAD,
                        fields=["symbol", "date", "close"])
    repo.save(source)
    assert repo.get("ds:pg-test").name == "PG测试"

    report = QualityReport(asset_id="ds:pg-test", checks=[
        QualityCheck(name="coverage", passed=True),
    ])
    repo.set_quality("ds:pg-test", report)
    assert repo.quality_of("ds:pg-test").passed is True

    repo.add_lineage(LineageEdge(from_asset="ds:pg-test", to_asset="ds:pg-derived"))
    edges = repo.lineage_of("ds:pg-test")
    assert len(edges) == 1 and edges[0].to_asset == "ds:pg-derived"

    catalog = PgAssetCatalog(sql_session_factory)
    assert catalog.search("PG")[0].asset_id == "ds:pg-test"
    assert catalog.upstream("ds:pg-derived") == ["ds:pg-test"]

    assert repo.delete("ds:pg-test") is True
    assert repo.get("ds:pg-test") is None
    assert repo.quality_of("ds:pg-test") is None
    assert repo.lineage_of("ds:pg-test") == []


def test_pg_import_batch_repository_roundtrip(sql_session_factory):
    repo = PgImportBatchRepository(sql_session_factory)
    batch = {
        "batch_id": "imp:sqltest",
        "filename": "x.csv",
        "name": "SQL测试",
        "owner": "alice",
        "kind": "csv_upload",
        "status": "uploaded",
        "source_asset_id": "ds:sql-test",
        "row_count": 1,
        "columns": ["symbol", "date"],
        "profile": {"normalized_rows": 1},
        "preview": [{"symbol": "SH600000"}],
        "quality_report": {"passed": True, "checks": []},
        "source_payload": {},
        "raw_bytes": b"symbol,date\n",
        "normalized_bytes": b"symbol,date\nSH600000,2021-01-01\n",
        "created_at": datetime.now(),
    }
    repo.create(batch)
    assert repo.get("imp:sqltest")["name"] == "SQL测试"
    assert repo.get_payloads("imp:sqltest") == (batch["raw_bytes"], batch["normalized_bytes"])

    repo.update("imp:sqltest", {"status": "approved", "reviewer": "bob"})
    assert repo.get("imp:sqltest")["status"] == "approved"
    assert len(repo.list(status="approved", owner="alice")) == 1
    assert len(repo.list(status="uploaded")) == 0

    assert repo.delete("imp:sqltest") is True
    assert repo.get("imp:sqltest") is None
