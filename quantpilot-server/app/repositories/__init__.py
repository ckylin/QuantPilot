"""Storage abstractions — Repository pattern so backend can swap in-memory → PG."""

from app.repositories.memory import InMemoryTaskRepository, InMemoryStrategyRepository
from app.repositories.base import TaskRepository, StrategyRepository

__all__ = [
    "TaskRepository", "StrategyRepository",
    "InMemoryTaskRepository", "InMemoryStrategyRepository",
]
