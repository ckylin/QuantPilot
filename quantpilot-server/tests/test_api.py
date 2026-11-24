"""Tests for the FastAPI layer — health, async submit, task polling, result, catalogs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import create_app
from app.api import deps as api_deps
from app.orchestration import TaskService
from app.services.executor.sandbox import RESULT_MARKER, SandboxExecutor
from app.services.llm.provider import OfflineProvider


def fake_runner(script_path, task_id, timeout):
    result = {
        "task_id": task_id, "status": "done",
        "metrics": {"annual_return": 0.12, "sharpe": 1.6, "max_drawdown": -0.07,
                    "win_rate": 0.55, "turnover": 0.3, "calmar": 1.7},
        "series": {"nav": [], "benchmark_nav": [], "drawdown": []},
        "contributions": [], "holdings": [], "logs_ref": "mem://log",
    }
    return RESULT_MARKER + json.dumps(result) + "\n", "", 0, False


@pytest.fixture()
def client() -> TestClient:
    svc = TaskService(
        provider=OfflineProvider(),
        executor=SandboxExecutor(runner="fake", fake_runner=fake_runner),
    )
    api_deps.set_task_service(svc)
    # 种子数据工坊/模型工坊（目录接口需要）
    from app.data import get_data_service, seed_catalog
    seed_catalog(get_data_service())
    from app.models import get_registry, seed_models
    seed_models(get_registry())
    return TestClient(create_app())


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_submit_and_result_flow(client):
    r = client.post("/api/v1/strategies", json={
        "intent": "沪深300里动量最强的20只，每月调仓",
        "client": {"device_id": "test"},
    })
    assert r.status_code == 202
    task_id = r.json()["task_id"]
    assert r.json()["status"] in ("pending", "running", "succeeded")

    # BackgroundTasks 在 TestClient 中同步执行，任务应已完成
    task = client.get(f"/api/v1/tasks/{task_id}").json()
    assert "task_id" in task and "status" in task
    assert "result" not in task  # 查询接口不含大体积结果

    result_resp = client.get(f"/api/v1/tasks/{task_id}/result")
    assert result_resp.status_code == 200
    result = result_resp.json()
    assert result["task_id"] == task_id
    assert result["strategy_type"] == "expression"


def test_result_before_success_returns_409(client):
    # 直接查一个不存在的任务
    r = client.get("/api/v1/tasks/not-exist/result")
    assert r.status_code == 404


def test_strategy_list_and_favorite(client):
    client.post("/api/v1/strategies", json={"intent": "动量策略"})
    items = client.get("/api/v1/strategies").json()["items"]
    assert len(items) >= 1
    sid = items[0]["id"]
    assert "result" not in items[0]

    r = client.post(f"/api/v1/strategies/{sid}/favorite", json={"favorite": True})
    assert r.status_code == 200 and r.json()["favorite"] is True
    favs = client.get("/api/v1/strategies", params={"favorite_only": True}).json()["items"]
    assert any(s["id"] == sid for s in favs)


def test_catalog_endpoints(client):
    factors = client.get("/api/v1/factors").json()
    assert factors["total"] >= 1
    assert factors["items"][0]["performance"]["ir"] is not None
    # IR 降序
    irs = [f["performance"]["ir"] or 0 for f in factors["items"]]
    assert irs == sorted(irs, reverse=True)

    models = client.get("/api/v1/models").json()
    assert any(m["model_id"] == "lgbm" for m in models["items"])
    assert all(not m["requires_gpu"] for m in models["items"])  # no_gpu 默认

    datasets = client.get("/api/v1/datasets").json()
    assert any(d["asset_id"] == "dataset:alpha158_cn" for d in datasets["items"])


def test_factors_tag_filter(client):
    r = client.get("/api/v1/factors", params={"tag": "动量"})
    assert r.status_code == 200
    assert all("动量" in f["tags"] for f in r.json()["items"])


def test_submit_validation(client):
    r = client.post("/api/v1/strategies", json={"intent": ""})
    assert r.status_code == 422  # Pydantic min_length
