"""Wire DataFoundry services to the configured storage backend."""

from __future__ import annotations

from app.core.config import settings
from app.db.repositories import ImportBatchRepository, MemoryImportBatchRepository

_memory_import_repo: MemoryImportBatchRepository | None = None


def has_database() -> bool:
    return bool(settings.database_url)


def build_data_service():
    """Build DataService backed by PG when configured, memory otherwise."""
    from app.data.catalog import set_catalog
    from app.data.factors import FactorStore, set_factor_store
    from app.data.service import DataService

    if not has_database():
        return DataService()

    from app.db.catalog import PgAssetCatalog
    from app.db.session import get_session_factory, init_db

    init_db()
    catalog = PgAssetCatalog(get_session_factory())
    factors = FactorStore(catalog)
    # Keep module-level singletons consistent for callers that use them directly.
    set_catalog(catalog)
    set_factor_store(factors)
    return DataService(catalog, factors)


def get_import_batch_repository() -> ImportBatchRepository:
    global _memory_import_repo
    if has_database():
        from app.db.repositories import PgImportBatchRepository
        from app.db.session import get_session_factory, init_db
        init_db()
        return PgImportBatchRepository(get_session_factory())
    if _memory_import_repo is None:
        _memory_import_repo = MemoryImportBatchRepository()
    return _memory_import_repo


def reset_factories() -> None:
    """Test helper: forget the in-memory import repository."""
    global _memory_import_repo
    _memory_import_repo = None
