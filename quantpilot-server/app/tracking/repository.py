"""Track 仓储：PG 实现 + 内存实现（无库/测试用）。

两个实现满足同一个 Protocol，``TrackService`` 不关心背后是 PG 还是内存。
默认走内存（与 ``QP_DATABASE_URL`` 为空时的既有行为一致），配了库自动落 PG。
"""

from __future__ import annotations

import copy
import threading
from datetime import date
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.tracking.models import (
    TrackCheckpointRow,
    TrackEventRow,
    TrackNavRow,
    TrackPositionRow,
    TrackRow,
    TrackSignalRow,
)

ACTIVE_STATES = ("observing", "paused", "shadow")

_NAV_FIELDS = (
    "nav", "bench_nav", "excess", "drawdown", "turnover", "fee",
    "cash", "equity", "positions_count", "data_snapshot_id",
)


class TrackRepository(Protocol):
    def create(self, track: dict[str, Any]) -> dict[str, Any]: ...
    def get(self, track_id: str) -> dict[str, Any] | None: ...
    def update(self, track_id: str, patch: dict[str, Any]) -> dict[str, Any] | None: ...
    def list(self, owner: str | None = None, state: str | None = None,
             limit: int = 50) -> list[dict[str, Any]]: ...
    def count_active(self, owner: str) -> int: ...

    def save_signal(self, signal: dict[str, Any]) -> None: ...
    def get_signal(self, track_id: str, run_date: date) -> dict[str, Any] | None: ...
    def replace_positions(self, track_id: str, run_date: date,
                          rows: list[dict[str, Any]]) -> None: ...
    def positions_of(self, track_id: str, run_date: date) -> list[dict[str, Any]]: ...

    def save_nav(self, nav: dict[str, Any]) -> None: ...
    def navs(self, track_id: str, limit: int | None = None) -> list[dict[str, Any]]: ...

    def add_event(self, event: dict[str, Any]) -> None: ...
    def events(self, track_id: str, limit: int = 200) -> list[dict[str, Any]]: ...

    def save_checkpoint(self, checkpoint: dict[str, Any]) -> None: ...
    def checkpoints(self, track_id: str) -> list[dict[str, Any]]: ...
    def get_checkpoint(self, track_id: str, day_n: int) -> dict[str, Any] | None: ...

    def clear(self) -> None: ...


# ---------------------------------------------------------------------------
# PostgreSQL / SQLite
# ---------------------------------------------------------------------------

def _track_to_dict(row: TrackRow) -> dict[str, Any]:
    return {
        "track_id": row.track_id,
        "strategy_id": row.strategy_id,
        "owner": row.owner,
        "name": row.name,
        "spec": row.spec,
        "rules": row.rules,
        "state": row.state,
        "start_date": row.start_date,
        "horizon_days": row.horizon_days,
        "rebalance_freq": row.rebalance_freq,
        "initial_capital": row.initial_capital,
        "cash": row.cash,
        "shares": row.shares,
        "avg_cost": row.avg_cost,
        "pending_targets": row.pending_targets,
        "bench_base": row.bench_base,
        "bench_last": row.bench_last,
        "last_run_date": row.last_run_date,
        "trading_days": row.trading_days,
        "consecutive_gaps": row.consecutive_gaps,
        "flat_days": row.flat_days,
        "stale_days": row.stale_days,
        "last_target_key": row.last_target_key,
        "evidence_level": row.evidence_level,
        "verdict": row.verdict,
        "alerted": row.alerted,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "archived_at": row.archived_at,
    }


class PgTrackRepository:
    """PostgreSQL（测试用 SQLite 也行）。"""

    def __init__(self, session_factory: sessionmaker[Session]):
        self._sf = session_factory

    def create(self, track: dict[str, Any]) -> dict[str, Any]:
        row = TrackRow(**track)
        with self._sf() as s:
            s.add(row)
            s.commit()
            s.refresh(row)
            return _track_to_dict(row)

    def get(self, track_id: str) -> dict[str, Any] | None:
        with self._sf() as s:
            row = s.get(TrackRow, track_id)
            return _track_to_dict(row) if row else None

    def update(self, track_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        with self._sf() as s:
            row = s.get(TrackRow, track_id)
            if row is None:
                return None
            for key, value in patch.items():
                if hasattr(row, key):
                    setattr(row, key, value)
            s.commit()
            s.refresh(row)
            return _track_to_dict(row)

    def list(self, owner: str | None = None, state: str | None = None,
             limit: int = 50) -> list[dict[str, Any]]:
        with self._sf() as s:
            stmt = select(TrackRow).order_by(TrackRow.created_at.desc()).limit(limit)
            if owner:
                stmt = stmt.where(TrackRow.owner == owner)
            if state:
                stmt = stmt.where(TrackRow.state == state)
            return [_track_to_dict(r) for r in s.scalars(stmt).all()]

    def count_active(self, owner: str) -> int:
        with self._sf() as s:
            stmt = select(func.count()).select_from(TrackRow).where(
                TrackRow.owner == owner, TrackRow.state.in_(ACTIVE_STATES)
            )
            return int(s.scalar(stmt) or 0)

    def save_signal(self, signal: dict[str, Any]) -> None:
        with self._sf() as s:
            row = s.get(TrackSignalRow, (signal["track_id"], signal["run_date"]))
            if row is None:
                s.add(TrackSignalRow(**signal))
            else:
                row.targets = signal["targets"]
                row.scores = signal["scores"]
                row.data_snapshot_id = signal["data_snapshot_id"]
            s.commit()

    def get_signal(self, track_id: str, run_date: date) -> dict[str, Any] | None:
        with self._sf() as s:
            row = s.get(TrackSignalRow, (track_id, run_date))
            if row is None:
                return None
            return {
                "track_id": row.track_id, "run_date": row.run_date,
                "targets": row.targets, "scores": row.scores,
                "data_snapshot_id": row.data_snapshot_id,
                "created_at": row.created_at,
            }

    def replace_positions(self, track_id: str, run_date: date,
                          rows: list[dict[str, Any]]) -> None:
        with self._sf() as s:
            s.execute(TrackPositionRow.__table__.delete().where(
                TrackPositionRow.track_id == track_id,
                TrackPositionRow.run_date == run_date,
            ))
            for item in rows:
                s.add(TrackPositionRow(track_id=track_id, run_date=run_date, **item))
            s.commit()

    def positions_of(self, track_id: str, run_date: date) -> list[dict[str, Any]]:
        with self._sf() as s:
            stmt = select(TrackPositionRow).where(
                TrackPositionRow.track_id == track_id,
                TrackPositionRow.run_date == run_date,
            ).order_by(TrackPositionRow.symbol)
            return [{
                "symbol": r.symbol, "qty": r.qty, "avg_cost": r.avg_cost,
                "close": r.close, "market_value": r.market_value, "weight": r.weight,
            } for r in s.scalars(stmt).all()]

    def save_nav(self, nav: dict[str, Any]) -> None:
        with self._sf() as s:
            row = s.get(TrackNavRow, (nav["track_id"], nav["run_date"]))
            values = {k: nav.get(k) for k in _NAV_FIELDS}
            if row is None:
                s.add(TrackNavRow(track_id=nav["track_id"], run_date=nav["run_date"], **values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            s.commit()

    def navs(self, track_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        with self._sf() as s:
            stmt = select(TrackNavRow).where(
                TrackNavRow.track_id == track_id
            ).order_by(TrackNavRow.run_date)
            rows = s.scalars(stmt).all()
            items = [{"track_id": r.track_id, "run_date": r.run_date,
                      **{k: getattr(r, k) for k in _NAV_FIELDS}} for r in rows]
        return items[-limit:] if limit else items

    def add_event(self, event: dict[str, Any]) -> None:
        with self._sf() as s:
            s.add(TrackEventRow(**event))
            s.commit()

    def events(self, track_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._sf() as s:
            stmt = select(TrackEventRow).where(
                TrackEventRow.track_id == track_id
            ).order_by(TrackEventRow.created_at.desc(), TrackEventRow.id.desc()).limit(limit)
            rows = s.scalars(stmt).all()
            return [{
                "id": r.id, "kind": r.kind, "message": r.message,
                "payload": r.payload, "created_at": r.created_at,
            } for r in rows]

    def save_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        with self._sf() as s:
            row = s.get(TrackCheckpointRow, (checkpoint["track_id"], checkpoint["day_n"]))
            if row is None:
                s.add(TrackCheckpointRow(**checkpoint))
            else:
                row.metrics = checkpoint["metrics"]
                row.evidence_level = checkpoint["evidence_level"]
                row.verdict = checkpoint["verdict"]
                row.run_date = checkpoint["run_date"]
            s.commit()

    def checkpoints(self, track_id: str) -> list[dict[str, Any]]:
        with self._sf() as s:
            stmt = select(TrackCheckpointRow).where(
                TrackCheckpointRow.track_id == track_id
            ).order_by(TrackCheckpointRow.day_n)
            return [{
                "day_n": r.day_n, "run_date": r.run_date, "metrics": r.metrics,
                "evidence_level": r.evidence_level, "verdict": r.verdict,
            } for r in s.scalars(stmt).all()]

    def get_checkpoint(self, track_id: str, day_n: int) -> dict[str, Any] | None:
        with self._sf() as s:
            row = s.get(TrackCheckpointRow, (track_id, day_n))
            if row is None:
                return None
            return {
                "day_n": row.day_n, "run_date": row.run_date, "metrics": row.metrics,
                "evidence_level": row.evidence_level, "verdict": row.verdict,
            }

    def clear(self) -> None:
        with self._sf() as s:
            for model in (TrackCheckpointRow, TrackEventRow, TrackNavRow,
                          TrackPositionRow, TrackSignalRow, TrackRow):
                s.execute(model.__table__.delete())
            s.commit()


# ---------------------------------------------------------------------------
# 内存实现（离线 / 测试）
# ---------------------------------------------------------------------------

class MemoryTrackRepository:
    def __init__(self) -> None:
        self._tracks: dict[str, dict[str, Any]] = {}
        self._signals: dict[tuple[str, date], dict[str, Any]] = {}
        self._positions: dict[tuple[str, date], list[dict[str, Any]]] = {}
        self._navs: dict[str, dict[date, dict[str, Any]]] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._checkpoints: dict[str, dict[int, dict[str, Any]]] = {}
        self._seq = 0
        self._lock = threading.RLock()

    def create(self, track: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._tracks[track["track_id"]] = copy.deepcopy(track)
            return copy.deepcopy(track)

    def get(self, track_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._tracks.get(track_id)
            return copy.deepcopy(item) if item else None

    def update(self, track_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            item = self._tracks.get(track_id)
            if item is None:
                return None
            item.update(copy.deepcopy(patch))
            return copy.deepcopy(item)

    def list(self, owner: str | None = None, state: str | None = None,
             limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            items = sorted(self._tracks.values(),
                           key=lambda t: str(t.get("created_at", "")), reverse=True)
            if owner:
                items = [t for t in items if t.get("owner") == owner]
            if state:
                items = [t for t in items if t.get("state") == state]
            return [copy.deepcopy(t) for t in items[:limit]]

    def count_active(self, owner: str) -> int:
        with self._lock:
            return sum(
                1 for t in self._tracks.values()
                if t.get("owner") == owner and t.get("state") in ACTIVE_STATES
            )

    def save_signal(self, signal: dict[str, Any]) -> None:
        with self._lock:
            self._signals[(signal["track_id"], signal["run_date"])] = copy.deepcopy(signal)

    def get_signal(self, track_id: str, run_date: date) -> dict[str, Any] | None:
        with self._lock:
            item = self._signals.get((track_id, run_date))
            return copy.deepcopy(item) if item else None

    def replace_positions(self, track_id: str, run_date: date,
                          rows: list[dict[str, Any]]) -> None:
        with self._lock:
            self._positions[(track_id, run_date)] = copy.deepcopy(rows)

    def positions_of(self, track_id: str, run_date: date) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._positions.get((track_id, run_date), []))

    def save_nav(self, nav: dict[str, Any]) -> None:
        with self._lock:
            self._navs.setdefault(nav["track_id"], {})[nav["run_date"]] = copy.deepcopy(nav)

    def navs(self, track_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            days = self._navs.get(track_id, {})
            items = [copy.deepcopy(days[d]) for d in sorted(days)]
        return items[-limit:] if limit else items

    def add_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._seq += 1
            item = copy.deepcopy(event)
            item.setdefault("id", self._seq)
            self._events.setdefault(event["track_id"], []).append(item)

    def events(self, track_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            items = list(reversed(self._events.get(track_id, [])))
            return copy.deepcopy(items[:limit])

    def save_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        with self._lock:
            self._checkpoints.setdefault(checkpoint["track_id"], {})[
                checkpoint["day_n"]
            ] = copy.deepcopy(checkpoint)

    def checkpoints(self, track_id: str) -> list[dict[str, Any]]:
        with self._lock:
            items = self._checkpoints.get(track_id, {})
            return [copy.deepcopy(items[k]) for k in sorted(items)]

    def get_checkpoint(self, track_id: str, day_n: int) -> dict[str, Any] | None:
        with self._lock:
            item = self._checkpoints.get(track_id, {}).get(day_n)
            return copy.deepcopy(item) if item else None

    def clear(self) -> None:
        with self._lock:
            self._tracks.clear()
            self._signals.clear()
            self._positions.clear()
            self._navs.clear()
            self._events.clear()
            self._checkpoints.clear()
