"""策略观察期（Track）测试：生命周期 / 记账 / 幂等 / 断档 / 判定。

关键断言都是"诚实性"约定，不是数值细节：
- 同一个交易日重跑不会二次累加天数；
- 取不到行情必须留 gap 事件，不能造一行假净值；
- 涨停买不进、跌停卖不掉要记下来，而不是假装成交；
- 没到窗口就下结论 = 失败。
"""

from __future__ import annotations

import copy
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.strategy_spec import StrategySpec
from app.tracking import (
    Bar,
    DayContext,
    EngineUnavailable,
    MemoryTrackRepository,
    TrackQuotaExceeded,
    TrackService,
    TrackStateError,
)
from app.tracking.rules import (
    build_targets,
    default_horizon,
    default_rules,
    estimated_verdict_date,
    is_rebalance_day,
)

BASE = {
    "spec_version": "0.1",
    "meta": {"name": "测试策略", "intent": "测试", "tags": ["momentum"]},
    "universe": {"type": "builtin", "name": "csi300"},
    "date_range": {"start": "2020-01-01", "end": "2024-12-31"},
    "rebalance": {"freq": "month"},
    "signal": {"type": "expression", "expression": "$close / Ref($close, 20) - 1",
               "direction": "desc"},
    "portfolio": {"selection": "topk", "topk": 2, "weighting": "equal"},
    "benchmark": "csi300",
    "account": {"initial_capital": 1_000_000},
}

D1, D2, D3 = date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4)


def spec_of(freq: str = "month", **overrides) -> dict:
    raw = copy.deepcopy(BASE)
    raw["rebalance"] = {"freq": freq}
    raw.update(overrides)
    return StrategySpec.model_validate(raw).model_dump(mode="json")


def bar(symbol: str, open_: float, close: float, prev: float | None = None,
        **kwargs) -> Bar:
    return Bar(symbol=symbol, open=open_, close=close, prev_close=prev or open_, **kwargs)


def ctx(day: date, bars: list[Bar], bench: float | None = 100.0) -> DayContext:
    return DayContext(trade_date=day, bars={b.symbol: b for b in bars},
                      bench_close=bench, data_snapshot_id="test")


class FakeEngine:
    """按日期回放行情 + 按日期给分数。"""

    name = "fake"

    def __init__(self, days: dict[date, DayContext], scores: dict[date, dict[str, float]]):
        self.days = days
        self.scores = scores

    def load_day(self, track, trade_date):
        day = self.days.get(trade_date)
        if day is None:
            raise EngineUnavailable(f"{trade_date} 没有行情数据")
        return day

    def score(self, track, ctx):
        return dict(self.scores.get(ctx.trade_date, {}))


@pytest.fixture()
def service() -> TrackService:
    return TrackService(MemoryTrackRepository(), engine=FakeEngine({}, {}))


def make_engine() -> FakeEngine:
    return FakeEngine(
        days={
            D1: ctx(D1, [bar("SH600000", 10, 10), bar("SZ000001", 10, 10)]),
            D2: ctx(D2, [bar("SH600000", 10, 11), bar("SZ000001", 10, 9)], bench=101.0),
            D3: ctx(D3, [bar("SH600000", 11, 11), bar("SZ000001", 9, 9)], bench=102.0),
        },
        scores={
            D1: {"SH600000": 2.0, "SZ000001": 1.0},
            D2: {"SH600000": 2.0, "SZ000001": 1.0},
            D3: {"SH600000": 1.0, "SZ000001": 2.0},
        },
    )


# ---------------------------------------------------------------------------
# 规则层
# ---------------------------------------------------------------------------

def test_default_horizon_scales_with_rebalance():
    assert default_horizon("day") == 20
    assert default_horizon("week") == 30
    assert default_horizon("month") == 90


def test_default_rules_lock_horizon_and_checkpoints():
    rules = default_rules("month")
    assert rules["horizon_days"] == 90
    assert rules["checkpoints"] == [7, 20, 60]
    assert rules["fail_excess"] == -0.08


def test_estimated_verdict_date_skips_weekend():
    # 2026-11-06 是周五，往后 2 个交易日 = 11-10（跳过 7/8 周末）
    assert estimated_verdict_date(date(2026, 11, 6), 2) == date(2026, 11, 10)


def test_rebalance_day_uses_first_run_of_period():
    assert is_rebalance_day("month", date(2026, 11, 30), None) is True
    assert is_rebalance_day("month", date(2026, 11, 30), date(2026, 11, 2)) is False
    assert is_rebalance_day("month", date(2026, 12, 1), date(2026, 11, 30)) is True
    assert is_rebalance_day("week", date(2026, 11, 6), date(2026, 11, 2)) is False
    assert is_rebalance_day("week", date(2026, 11, 9), date(2026, 11, 6)) is True
    assert is_rebalance_day("day", date(2026, 11, 3), date(2026, 11, 2)) is True


def test_build_targets_caps_without_renormalising():
    spec = spec_of()
    spec["portfolio"]["max_single_weight"] = 0.3
    targets = build_targets(spec, {"SH600000": 3.0, "SZ000001": 2.0})
    assert len(targets) == 2
    assert all(t["weight"] <= 0.3 + 1e-9 for t in targets)
    # 封顶后不归一 → 总额小于 1，缺口留现金（刻意的）
    assert sum(t["weight"] for t in targets) < 1.0


def test_build_targets_respects_asc_direction():
    spec = spec_of()
    spec["signal"]["direction"] = "asc"
    targets = build_targets(spec, {"A": 3.0, "B": 1.0})
    # 只有两个标的且 topk=2，顺序应升序：B 在前
    assert targets[0]["symbol"] == "B"


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------

def test_create_track_starts_observing_and_explains_way_of_work(service):
    track = service.create_track(spec_of("month"), owner="u1")
    assert track["state"] == "observing"
    assert track["trading_days"] == 0
    assert track["evidence_level"] == "L0"
    assert track["horizon_days"] == 90
    assert track["progress"]["days_left"] == 90
    assert track["progress"]["note"]

    journal = service.journal(track["track_id"])
    assert journal[0]["kind"] == "created"
    assert "90 个交易日" in journal[0]["message"]


def test_quota_blocks_fourth_track(service):
    for _ in range(3):
        service.create_track(spec_of("month"), owner="u1")
    with pytest.raises(TrackQuotaExceeded):
        service.create_track(spec_of("month"), owner="u1")


def test_rejected_backtest_does_not_enter_observation(service):
    track = service.create_track(
        spec_of(), owner="u1", backtest={"metrics": {"annual_return": -0.03}}
    )
    assert track["state"] == "rejected"
    # 被拒的不占观察额度
    assert service.repo.count_active("u1") == 0


def test_pause_blocks_run_and_resume_restores(service):
    track = service.create_track(spec_of("day"), owner="u1")
    tid = track["track_id"]
    service.pause(tid)
    with pytest.raises(TrackStateError):
        service.run_day(tid, D1, engine=make_engine())
    service.resume(tid)
    service.run_day(tid, D1, engine=make_engine())
    assert service.get(tid)["state"] == "observing"


def test_archive_and_restore(service):
    track = service.create_track(spec_of("day"), owner="u1")
    tid = track["track_id"]
    assert service.archive(tid)["state"] == "archived"
    assert service.restore(tid)["state"] == "observing"


# ---------------------------------------------------------------------------
# 单日执行
# ---------------------------------------------------------------------------

def test_first_day_marks_nav_one_and_creates_pending(service):
    track = service.create_track(spec_of("day"), owner="u1", horizon_days=20)
    tid = track["track_id"]
    out = service.run_day(tid, D1, engine=make_engine())

    assert out["ok"] is True
    assert out["nav"] == pytest.approx(1.0)
    assert out["positions_count"] == 0        # 第一天只出信号，不成交
    assert out["pending_targets"] == 2

    latest = service.get(tid)
    assert latest["trading_days"] == 1
    assert latest["last_run_date"] == D1
    assert latest["evidence_level"] == "L1"
    assert latest["progress"]["percent"] == 5


def test_second_day_executes_pending_at_open(service):
    track = service.create_track(spec_of("day"), owner="u1")
    tid = track["track_id"]
    engine = make_engine()
    service.run_day(tid, D1, engine=engine)
    out = service.run_day(tid, D2, engine=engine)

    assert out["ok"] is True
    assert out["positions_count"] == 2
    assert all(f["action"] == "buy" and f["qty"] > 0 for f in out["fills"])
    assert out["nav"] != pytest.approx(1.0)

    positions = service.positions(tid)
    assert {p["symbol"] for p in positions} == {"SH600000", "SZ000001"}
    assert all(p["qty"] % 100 == 0 for p in positions)   # 整手

    navs = service.navs(tid)
    assert navs[-1]["positions_count"] == 2
    assert navs[-1]["nav"] > 0


def test_same_day_rerun_is_idempotent(service):
    track = service.create_track(spec_of("day"), owner="u1")
    tid = track["track_id"]
    engine = make_engine()
    service.run_day(tid, D1, engine=engine)
    first = service.run_day(tid, D2, engine=engine)
    second = service.run_day(tid, D2, engine=engine)

    assert first["nav"] == pytest.approx(second["nav"])
    assert service.get(tid)["trading_days"] == 2
    assert len(service.navs(tid)) == 2


def test_rerunning_earlier_date_is_rejected(service):
    track = service.create_track(spec_of("day"), owner="u1")
    tid = track["track_id"]
    engine = make_engine()
    service.run_day(tid, D1, engine=engine)
    service.run_day(tid, D2, engine=engine)
    with pytest.raises(TrackStateError):
        service.run_day(tid, D1, engine=engine)


def test_limit_up_blocks_buy_and_is_recorded():
    repo = MemoryTrackRepository()
    service = TrackService(repo, engine=FakeEngine({}, {}))
    track = service.create_track(spec_of("day"), owner="u1")
    tid = track["track_id"]

    blocked = FakeEngine(
        days={
            D1: ctx(D1, [bar("SH600000", 10, 10), bar("SZ000001", 10, 10)]),
            D2: ctx(D2, [
                bar("SH600000", 11, 11, prev=10, limit_up=11.0),   # 开盘即涨停
                bar("SZ000001", 10, 10),
            ]),
        },
        scores={D1: {"SH600000": 2.0, "SZ000001": 1.0}},
    )
    service.run_day(tid, D1, engine=blocked)
    out = service.run_day(tid, D2, engine=blocked)

    reasons = {f.get("reason") for f in out["fills"]}
    assert "limit_up" in reasons
    held = {p["symbol"] for p in service.positions(tid)}
    assert "SH600000" not in held


def test_unfilled_leaves_cash_instead_of_forcing_full_position():
    repo = MemoryTrackRepository()
    service = TrackService(repo, engine=FakeEngine({}, {}))
    track = service.create_track(spec_of("day"), owner="u1")
    tid = track["track_id"]
    engine = FakeEngine(
        days={
            D1: ctx(D1, [bar("SH600000", 10, 10), bar("SZ000001", 10, 10)]),
            D2: ctx(D2, [
                bar("SH600000", 11, 11, prev=10, suspended=True),
                bar("SZ000001", 10, 10),
            ]),
        },
        scores={D1: {"SH600000": 2.0, "SZ000001": 1.0}},
    )
    service.run_day(tid, D1, engine=engine)
    service.run_day(tid, D2, engine=engine)
    assert {p["symbol"] for p in service.positions(tid)} == {"SZ000001"}


# ---------------------------------------------------------------------------
# 断档与判定
# ---------------------------------------------------------------------------

def test_missing_market_data_records_gap_not_fake_nav(service):
    track = service.create_track(spec_of("day"), owner="u1")
    tid = track["track_id"]
    out = service.run_day(tid, date(2026, 11, 5), engine=make_engine())

    assert out["ok"] is False
    assert "没有行情数据" in out["reason"]
    assert service.get(tid)["trading_days"] == 0
    assert service.navs(tid) == []

    kinds = [e["kind"] for e in service.journal(tid)]
    assert "gap" in kinds


def test_three_consecutive_gaps_auto_pause(service):
    track = service.create_track(spec_of("day"), owner="u1")
    tid = track["track_id"]
    engine = make_engine()
    service.run_day(tid, D1, engine=engine)
    for day in (date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 11)):
        out = service.run_day(tid, day, engine=engine)
        assert out["ok"] is False
    assert service.get(tid)["state"] == "auto_paused"


def test_checkpoint_writes_human_verdict(service):
    track = service.create_track(
        spec_of("day"), owner="u1", rules={"checkpoints": [1]}
    )
    tid = track["track_id"]
    service.run_day(tid, D1, engine=make_engine())

    checkpoints = service.get(tid)["checkpoints"]
    assert len(checkpoints) == 1
    assert checkpoints[0]["day_n"] == 1
    assert "1 个交易日" in checkpoints[0]["verdict"]
    assert "还没" in checkpoints[0]["verdict"] or "太早" in checkpoints[0]["verdict"]


def test_horizon_reached_concludes_with_evidence_level(service):
    track = service.create_track(spec_of("day"), owner="u1", horizon_days=1)
    tid = track["track_id"]
    service.run_day(tid, D1, engine=make_engine())
    latest = service.get(tid)

    assert latest["state"] == "concluded"
    assert latest["verdict"]
    assert latest["evidence_level"] == "L1"
    assert latest["progress"]["days_left"] == 0


def test_no_silent_fabrication_when_engine_is_default():
    """默认引擎未接日更行情 → 必须报不可用，而不是造数据。"""
    service = TrackService(MemoryTrackRepository())
    track = service.create_track(spec_of("day"), owner="u1")
    out = service.run_day(track["track_id"], D1)
    assert out["ok"] is False
    assert "行情管道" in out["reason"]


def test_sql_repository_roundtrip_keeps_null_pending():
    """PG/SQLite 实现必须能把 pending_targets=None 原样存回（否则会误清仓）。"""
    from app.db.session import (
        configure_database,
        get_session_factory,
        init_db,
        reset_database,
    )
    from app.tracking.repository import PgTrackRepository

    engine = configure_database("sqlite://")
    try:
        init_db(engine)
        service = TrackService(PgTrackRepository(get_session_factory()),
                               engine=FakeEngine({}, {}))
        track = service.create_track(spec_of("day"), owner="pg")
        tid = track["track_id"]
        assert service.get(tid)["pending_targets"] is None

        service.run_day(tid, D1, engine=make_engine())
        service.run_day(tid, D2, engine=make_engine())

        latest = service.get(tid)
        assert latest["trading_days"] == 2
        assert latest["last_run_date"] == D2
        assert len(service.navs(tid)) == 2
        assert len(service.positions(tid)) == 2
        assert any(e["kind"] == "day" for e in service.journal(tid))
    finally:
        reset_database()
