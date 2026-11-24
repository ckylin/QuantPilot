"""Track API 契约测试：开观察 → 跑一天 → 看 journal → 暂停。"""

from __future__ import annotations

import copy
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import create_app
from app.api import deps as api_deps
from app.tracking import (
    Bar,
    DayContext,
    EngineUnavailable,
    MemoryTrackRepository,
    TrackService,
)

D1 = date(2026, 11, 2)
D2 = date(2026, 11, 3)

SPEC = {
    "spec_version": "0.1",
    "meta": {"name": "API 测试策略", "intent": "测试", "tags": ["momentum"]},
    "universe": {"type": "builtin", "name": "csi300"},
    "date_range": {"start": "2020-01-01", "end": "2024-12-31"},
    "rebalance": {"freq": "day"},
    "signal": {"type": "expression", "expression": "$close / Ref($close, 20) - 1",
               "direction": "desc"},
    "portfolio": {"selection": "topk", "topk": 2, "weighting": "equal"},
    "benchmark": "csi300",
    "account": {"initial_capital": 1_000_000},
}


class FakeEngine:
    name = "fake"

    def load_day(self, track, trade_date):
        if trade_date == D1:
            bars = [Bar("SH600000", 10, 10, 10), Bar("SZ000001", 10, 10, 10)]
        elif trade_date == D2:
            bars = [Bar("SH600000", 10, 11, 10), Bar("SZ000001", 10, 9, 10)]
        else:
            raise EngineUnavailable(f"{trade_date} 没有行情")
        return DayContext(trade_date=trade_date,
                          bars={b.symbol: b for b in bars}, bench_close=100.0)

    def score(self, track, ctx):
        return {"SH600000": 2.0, "SZ000001": 1.0}


@pytest.fixture()
def client() -> TestClient:
    api_deps.set_track_service(
        TrackService(MemoryTrackRepository(), engine=FakeEngine(), active_limit=3)
    )
    return TestClient(create_app())


def create(client, spec: dict | None = None) -> dict:
    r = client.post("/api/v1/tracks", json={
        "spec": copy.deepcopy(spec or SPEC), "owner": "api-user",
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_create_track_returns_progress(client):
    body = create(client)
    assert body["state"] == "observing"
    assert body["track_id"].startswith("trk-")
    assert body["progress"]["horizon_days"] == 20
    assert body["progress"]["days_left"] == 20
    assert body["progress"]["note"]


def test_create_requires_spec_or_strategy_id(client):
    r = client.post("/api/v1/tracks", json={"owner": "x"})
    assert r.status_code == 422


def test_run_day_and_read_journal(client):
    track = create(client)
    tid = track["track_id"]

    r = client.post(f"/api/v1/tracks/{tid}/run", json={"trade_date": D1.isoformat()})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["nav"] == pytest.approx(1.0)
    assert body["pending_targets"] == 2

    r = client.post(f"/api/v1/tracks/{tid}/run", json={"trade_date": D2.isoformat()})
    assert r.status_code == 200
    assert r.json()["positions_count"] == 2

    journal = client.get(f"/api/v1/tracks/{tid}/journal").json()
    kinds = [e["kind"] for e in journal["items"]]
    assert "created" in kinds and "day" in kinds

    nav = client.get(f"/api/v1/tracks/{tid}/nav").json()
    assert nav["total"] == 2

    positions = client.get(f"/api/v1/tracks/{tid}/positions").json()
    assert positions["total"] == 2


def test_pause_then_run_returns_conflict(client):
    tid = create(client)["track_id"]
    assert client.post(f"/api/v1/tracks/{tid}/pause", json={}).status_code == 200
    r = client.post(f"/api/v1/tracks/{tid}/run", json={"trade_date": D1.isoformat()})
    assert r.status_code == 409
    assert client.post(f"/api/v1/tracks/{tid}/resume", json={}).status_code == 200


def test_quota_returns_conflict(client):
    for _ in range(3):
        create(client)
    r = client.post("/api/v1/tracks", json={"spec": copy.deepcopy(SPEC), "owner": "api-user"})
    assert r.status_code == 409
    assert "最多 3 个" in r.json()["detail"]


def test_unknown_track_returns_404(client):
    assert client.get("/api/v1/tracks/trk-nope").status_code == 404
    assert client.post("/api/v1/tracks/trk-nope/run", json={}).status_code == 404


def test_missing_data_returns_ok_false_not_500(client):
    tid = create(client)["track_id"]
    r = client.post(f"/api/v1/tracks/{tid}/run",
                    json={"trade_date": "2026-11-09"})
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "reason" in r.json()
