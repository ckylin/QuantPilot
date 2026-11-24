"""SQLAlchemy models for strategy tracking (Track).

共享 ``app.db.models.Base``，所以 ``init_db()`` 会与数据工坊的表一起创建。

设计要点（见 docs/design-baseline.md §4）：
- ``qp_tracks`` 一行 = 一个观察实例。**热状态**（现金/持仓/待执行目标）直接放行内 JSON，
  避免每次日更都要 join 明细表；
- 明细表（signal / position / nav / event / checkpoint）只追加，作为历史与 journal 数据源；
- 净值点必须绑 ``data_snapshot_id``：行情修订时只追加修正事件，不重写历史。
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class TrackRow(Base):
    """一个策略观察实例（Track）。"""

    __tablename__ = "qp_tracks"

    track_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    owner: Mapped[str] = mapped_column(String(80), nullable=False, default="local", index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    rules: Mapped[dict] = mapped_column(JSON, nullable=False)

    state: Mapped[str] = mapped_column(String(24), nullable=False, default="observing", index=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    rebalance_freq: Mapped[str] = mapped_column(String(16), nullable=False, default="month")

    # 纸面账本（热状态）
    initial_capital: Mapped[float] = mapped_column(Float, nullable=False, default=1_000_000.0)
    cash: Mapped[float] = mapped_column(Float, nullable=False, default=1_000_000.0)
    shares: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    avg_cost: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # None = 没有待执行订单；[] = 明确清仓。两者不能混（混了就会非调仓日误清仓）
    pending_targets: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # 基准净值要一个锚点：第一次拿到 bench_close 时定为 base，之后按比例推
    bench_base: Mapped[float | None] = mapped_column(Float, nullable=True)
    bench_last: Mapped[float | None] = mapped_column(Float, nullable=True)

    last_run_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    trading_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_gaps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    flat_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stale_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_target_key: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    evidence_level: Mapped[str] = mapped_column(String(8), nullable=False, default="L0")
    verdict: Mapped[str | None] = mapped_column(Text, nullable=True)
    alerted: Mapped[bool] = mapped_column(nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.now, onupdate=datetime.now
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


Index("ix_qp_tracks_owner_state", TrackRow.owner, TrackRow.state)


class TrackSignalRow(Base):
    """某日收盘后产生的目标组合（次日开盘执行，所以它是 pending 的来源）。"""

    __tablename__ = "qp_track_signals"

    track_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_date: Mapped[date] = mapped_column(Date, primary_key=True)
    targets: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    scores: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    data_snapshot_id: Mapped[str] = mapped_column(String(80), nullable=False, default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)


class TrackPositionRow(Base):
    """某日收盘后的纸面持仓快照。"""

    __tablename__ = "qp_track_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    track_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    close: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    market_value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


Index("ix_qp_track_positions_day", TrackPositionRow.track_id, TrackPositionRow.run_date)


class TrackNavRow(Base):
    """每日净值点。体量：250 行/年/Track，PG 无压力。"""

    __tablename__ = "qp_track_navs"

    track_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_date: Mapped[date] = mapped_column(Date, primary_key=True)
    nav: Mapped[float] = mapped_column(Float, nullable=False)
    bench_nav: Mapped[float | None] = mapped_column(Float, nullable=True)
    excess: Mapped[float | None] = mapped_column(Float, nullable=True)
    drawdown: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    turnover: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    fee: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cash: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    equity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    positions_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    data_snapshot_id: Mapped[str] = mapped_column(String(80), nullable=False, default="local")


class TrackEventRow(Base):
    """journal 的唯一数据源，append-only。"""

    __tablename__ = "qp_track_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    track_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="note")
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)


Index("ix_qp_track_events_track", TrackEventRow.track_id, TrackEventRow.created_at)


class TrackCheckpointRow(Base):
    """评估检查点（7/20/60 日），含证据等级与判定文案。"""

    __tablename__ = "qp_track_checkpoints"

    track_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    day_n: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_date: Mapped[date] = mapped_column(Date, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    evidence_level: Mapped[str] = mapped_column(String(8), nullable=False, default="L0")
    verdict: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
