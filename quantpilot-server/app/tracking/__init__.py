"""策略观察期（Track）：把一次性回测变成每天跑的验证过程。

对外入口：

    from app.tracking import TrackService, get_track_repository
"""

from app.tracking.engine import (
    Bar,
    DayContext,
    EngineUnavailable,
    SignalEngine,
    UnavailableEngine,
    get_signal_engine,
)
from app.tracking.repository import (
    MemoryTrackRepository,
    PgTrackRepository,
    TrackRepository,
)
from app.tracking.rules import (
    EVIDENCE_TEXT,
    build_targets,
    default_horizon,
    default_rules,
    estimated_verdict_date,
    evidence_level,
    is_rebalance_day,
)
from app.tracking.service import (
    ALLOWED_TRANSITIONS,
    TrackError,
    TrackNotFound,
    TrackQuotaExceeded,
    TrackService,
    TrackStateError,
)


def get_track_repository() -> TrackRepository:
    """有库走 PG，没库走内存（与数据工坊的既有策略一致）。"""
    from app.core.config import settings

    if settings.database_url:
        from app.db.session import get_session_factory, init_db
        init_db()
        return PgTrackRepository(get_session_factory())
    return _memory_repo()


_memory: MemoryTrackRepository | None = None


def _memory_repo() -> MemoryTrackRepository:
    global _memory
    if _memory is None:
        _memory = MemoryTrackRepository()
    return _memory


def reset_memory_repository() -> None:
    """测试辅助：丢掉内存仓储单例。"""
    global _memory
    _memory = None


__all__ = [
    "ALLOWED_TRANSITIONS",
    "Bar",
    "DayContext",
    "EVIDENCE_TEXT",
    "EngineUnavailable",
    "MemoryTrackRepository",
    "PgTrackRepository",
    "SignalEngine",
    "TrackError",
    "TrackNotFound",
    "TrackQuotaExceeded",
    "TrackRepository",
    "TrackService",
    "TrackStateError",
    "UnavailableEngine",
    "build_targets",
    "default_horizon",
    "default_rules",
    "estimated_verdict_date",
    "evidence_level",
    "get_signal_engine",
    "get_track_repository",
    "is_rebalance_day",
    "reset_memory_repository",
]
