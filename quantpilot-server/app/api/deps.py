"""Shared singletons for the API layer.

TaskService holds the in-memory repositories; a single instance keeps task and
strategy state consistent across requests. Swap the repository implementations
here when moving to Postgres/Redis.
"""

from __future__ import annotations

from app.core.config import settings
from app.orchestration import TaskService
from app.services.executor.sandbox import SandboxExecutor
from app.services.intent import IntentService
from app.tracking import TrackService, get_track_repository

_service: TaskService | None = None
_intent_service: IntentService | None = None
_track_service: TrackService | None = None


def get_intent_service() -> IntentService:
    """零代码入口：一句话/向导 → IntentFrame。单例，复用 provider。"""
    global _intent_service
    if _intent_service is None:
        _intent_service = IntentService()
    return _intent_service


def set_intent_service(service: IntentService | None) -> None:
    """测试注入点。"""
    global _intent_service
    _intent_service = service


def get_task_service() -> TaskService:
    global _service
    if _service is None:
        runner = settings.sandbox_runner
        # 无 LLM 凭证 = 离线演示模式：自动用 demo runner（不依赖容器/qlib）
        if not settings.llm_api_key and runner == "podman":
            runner = "demo"
        executor = SandboxExecutor(
            runner=runner,
            image=settings.sandbox_image,
            data_dir=settings.qlib_provider_uri,
        )
        _service = TaskService(executor=executor)
    return _service


def set_task_service(service: TaskService | None) -> None:
    """测试注入点。"""
    global _service
    _service = service


def get_track_service() -> TrackService:
    """策略观察期服务。有库落 PG，没库走内存。"""
    global _track_service
    if _track_service is None:
        _track_service = TrackService(get_track_repository())
    return _track_service


def set_track_service(service: TrackService | None) -> None:
    """测试注入点。"""
    global _track_service
    _track_service = service
