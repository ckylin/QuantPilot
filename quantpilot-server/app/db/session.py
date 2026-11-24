"""Database engine / session management.

Production uses PostgreSQL via ``QP_DATABASE_URL``. Tests can point the same
code at SQLite in-memory by calling ``configure_database("sqlite://")``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.models import Base

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def make_engine(url: str, echo: bool = False) -> Engine:
    if url.startswith("sqlite"):
        return create_engine(
            url,
            connect_args={"check_same_thread": False} if ":memory:" in url else {},
            poolclass=StaticPool,
            echo=echo,
            future=True,
        )
    return create_engine(url, pool_pre_ping=True, echo=echo, future=True)


def configure_database(url: str | None = None, echo: bool | None = None) -> Engine | None:
    """(Re)configure the global engine. Passing no URL disables SQL storage."""
    global _engine, _session_factory
    resolved = url if url is not None else settings.database_url
    if not resolved:
        _engine, _session_factory = None, None
        return None
    if echo is None:
        echo = settings.db_echo
    _engine = make_engine(resolved, echo=echo)
    _session_factory = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        configure_database()
    if _engine is None:
        raise RuntimeError(
            "未配置数据库。请设置 QP_DATABASE_URL，或在测试中调用 configure_database('sqlite://')。"
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(engine: Engine | None = None) -> None:
    """Create tables if they do not exist (idempotent)."""
    Base.metadata.create_all(bind=engine or get_engine())


def reset_database() -> None:
    """Test helper: drop global engine/session state."""
    global _engine, _session_factory
    _engine, _session_factory = None, None
