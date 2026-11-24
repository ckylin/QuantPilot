"""In-memory repository implementations (Phase 1 / tests / offline demo).

Thread-safe with a lock so FastAPI's worker threads can share state.
Replace with Postgres-backed classes in production; the Protocol stays the same.
"""

from __future__ import annotations

import threading
import uuid
from typing import Any

from app.repositories.base import StrategyRepository, TaskRepository


class InMemoryTaskRepository(TaskRepository):
    def __init__(self):
        self._data: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def save(self, task: dict[str, Any]) -> None:
        with self._lock:
            self._data[task["task_id"]] = dict(task)

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            t = self._data.get(task_id)
            return dict(t) if t else None

    def update(self, task_id: str, patch: dict[str, Any]) -> None:
        with self._lock:
            if task_id in self._data:
                self._data[task_id].update(patch)

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            items = sorted(self._data.values(),
                           key=lambda t: t.get("created_at", ""), reverse=True)
            return [dict(t) for t in items[:limit]]


class InMemoryStrategyRepository(StrategyRepository):
    def __init__(self):
        self._data: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def save(self, strategy: dict[str, Any]) -> str:
        with self._lock:
            sid = strategy.get("id") or f"STR-{uuid.uuid4().hex[:8]}"
            item = dict(strategy)
            item["id"] = sid
            self._data[sid] = item
            return sid

    def get(self, strategy_id: str) -> dict[str, Any] | None:
        with self._lock:
            s = self._data.get(strategy_id)
            return dict(s) if s else None

    def list(self, favorite_only: bool = False, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._data.values())
            if favorite_only:
                items = [s for s in items if s.get("favorite")]
            items = sorted(items, key=lambda s: s.get("created_at", ""), reverse=True)
            return [dict(s) for s in items[:limit]]

    def set_favorite(self, strategy_id: str, favorite: bool) -> None:
        with self._lock:
            if strategy_id in self._data:
                self._data[strategy_id]["favorite"] = favorite
