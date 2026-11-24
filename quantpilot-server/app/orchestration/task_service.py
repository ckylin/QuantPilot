"""TaskService — orchestrates the full pipeline: intent -> spec -> code -> sandbox -> result.

Uses the Repository abstractions for persistence and the state machine for
validated transitions. Runs synchronously here; the API layer submits it to a
background worker (Phase 1) so POST returns immediately.

Design decisions:
- Stage B uses deterministic template rendering as the primary path: the Spec is
  already fully structured, so the fixed runner needs no LLM invention. This is
  faster, safer and works offline. An LLM code path remains available in
  CodeGenerator.generate() for future use.
- Every stage writes progress back to the task repository so GET /tasks/{id}
  reflects real state.
- Failures set status=failed with an error message (no silent swallowing).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.repositories.base import StrategyRepository, TaskRepository
from app.repositories.memory import InMemoryStrategyRepository, InMemoryTaskRepository
from app.services.executor.sandbox import SandboxExecutor
from app.services.llm.ast_guard import check_code
from app.services.llm.codegen_service import CodeGenerator
from app.services.llm.prompt_loader import PromptLoader
from app.services.llm.provider import LLMProvider
from app.services.llm.provider_factory import get_provider
from app.services.llm.spec_service import SpecGenerator

from app.orchestration.state_machine import (
    TaskStatus,
    progress_for,
    transition,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskService:
    def __init__(
        self,
        provider: LLMProvider | None = None,
        task_repo: TaskRepository | None = None,
        strategy_repo: StrategyRepository | None = None,
        executor: SandboxExecutor | None = None,
    ):
        self.provider = provider or get_provider()
        self.prompts = PromptLoader()
        self.task_repo = task_repo or InMemoryTaskRepository()
        self.strategy_repo = strategy_repo or InMemoryStrategyRepository()
        self.executor = executor or SandboxExecutor()

    # ---- 提交 -------------------------------------------------------------

    def submit(
        self,
        intent: str,
        structured_params: dict[str, Any] | None = None,
        dataset_ref: str | None = None,
        model_id: str | None = None,
        client: dict[str, Any] | None = None,
        frame: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """创建任务并返回（不执行）。执行由 run() 或后台 worker 触发。

        ``frame`` 为引导式入口已确认的 IntentFrame；给了它就不再调用 LLM 猜意图。
        """
        task_id = f"task-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
        task = {
            "task_id": task_id,
            "status": "pending",
            "progress": 0,
            "stage": None,
            "intent": intent,
            "structured_params": structured_params or {},
            "dataset_ref": dataset_ref,
            "model_id": model_id,
            "client": client or {},
            "frame": frame,
            "error": None,
            "created_at": _now(),
            "finished_at": None,
            "result": None,
        }
        self.task_repo.save(task)
        return dict(task)

    # ---- 状态更新 ---------------------------------------------------------

    def _set_stage(self, task_id: str, target: TaskStatus, stage: str) -> None:
        task = self.task_repo.get(task_id)
        if not task:
            return
        current = task["status"]
        # 同一状态内仅更新 stage/progress，不算状态转移
        new_status = current if current == target else transition(current, target)  # type: ignore[arg-type]
        self.task_repo.update(task_id, {
            "status": new_status,
            "stage": stage,
            "progress": progress_for(stage),
        })

    def _fail(self, task_id: str, error: str) -> None:
        task = self.task_repo.get(task_id)
        if not task:
            return
        status = task["status"]
        # running/repairing 可直接 failed；其他状态按状态机推进
        try:
            new_status = transition(status, "failed")  # type: ignore[arg-type]
        except Exception:
            new_status = "failed"
        self.task_repo.update(task_id, {
            "status": new_status, "error": error, "finished_at": _now(),
        })

    # ---- 执行全流程 --------------------------------------------------------

    def run(self, task_id: str) -> dict[str, Any]:
        """同步执行任务全流程。返回最终 task（含 result 或 error）。"""
        task = self.task_repo.get(task_id)
        if not task:
            raise KeyError(f"任务不存在: {task_id}")

        try:
            # PENDING -> QUEUED -> RUNNING
            self._set_stage(task_id, "queued", "intent")
            self._set_stage(task_id, "running", "intent")

            # Stage A: 想法 -> Spec
            # 引导式入口（已确认 frame）走确定性编译，不消耗 LLM、不会理解跑偏；
            # 自由输入走 LLM 生成，失败时再退回规则解析。
            plan: dict[str, Any] | None = None
            if task.get("frame"):
                spec, plan = self._spec_from_frame(task)
            else:
                spec, _trace = SpecGenerator(self.provider, self.prompts).generate(
                    task["intent"], task.get("structured_params") or {}
                )
            # 资产引用覆盖：数据工坊数据集 + 模型工坊模型
            self._apply_asset_refs(spec, task)
            self.task_repo.update(task_id, {"spec": spec.model_dump(mode="json")})
            self._set_stage(task_id, "running", "spec")

            # Stage B: Spec -> 代码（确定性渲染为主）
            code = CodeGenerator(self.provider, self.prompts).render_deterministic(spec)
            extra = self._allowed_imports()
            passed, errors = check_code(code, self.prompts.code_template(spec.signal.type),
                                        extra_imports=extra)
            if not passed:
                raise RuntimeError(f"生成代码未通过 AST 检查: {errors}")
            self._set_stage(task_id, "running", "code")

            # 沙箱执行
            self._set_stage(task_id, "running", "backtest")
            execution = self.executor.execute(code, task_id=task_id)
            if not execution.success:
                raise RuntimeError(f"回测执行失败: {'; '.join(execution.errors)}")

            # 结果标准化 + 落库
            result = dict(execution.result or {})
            result.setdefault("strategy_type", spec.signal.type)
            # 结果必须携带完整 Spec（result-schema.md 契约）：前端用它显示
            # 策略名/类型/参数；执行器可能不返回 spec（如 demo runner），这里补全。
            result["spec"] = spec.model_dump(mode="json")
            result["task_id"] = task_id  # 执行器已注入，这里再确保
            # 零代码入口：把"我替你做了哪些决定"带给前端展示
            if plan:
                result["plan"] = plan
            self._set_stage(task_id, "running", "result")

            strategy_id = self.strategy_repo.save({
                "id": f"STR-{task_id[-6:]}",
                "name": spec.meta.name,
                "intent": task["intent"],
                "spec": spec.model_dump(mode="json"),
                "result": result,
                "favorite": False,
                "created_at": _now(),
            })
            result["strategy_id"] = strategy_id

            # RUNNING -> SUCCEEDED
            task = self.task_repo.get(task_id)
            self.task_repo.update(task_id, {
                "status": transition(task["status"], "succeeded"),  # type: ignore[arg-type]
                "result": result,
                "progress": 100,
                "finished_at": _now(),
            })
        except Exception as exc:
            self._fail(task_id, str(exc))

        return self.get_task(task_id)

    # ---- 零代码入口 -------------------------------------------------------

    def _spec_from_frame(self, task: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        """IntentFrame → Spec（确定性）。返回 (spec, plan)。"""
        from app.services.intent import FrameCompiler, IntentFrame

        frame = IntentFrame.model_validate(task["frame"])
        compiled = FrameCompiler().compile(frame, intent=task["intent"])
        plan = {
            "card": compiled.card,
            "assumptions": compiled.assumptions,
            "caveats": compiled.caveats,
        }
        return compiled.spec, plan

    # ---- 资产引用 ---------------------------------------------------------

    def _apply_asset_refs(self, spec: Any, task: dict[str, Any]) -> None:
        """把 dataset_ref / model_id 覆盖进 Spec。"""
        dataset_ref = task.get("dataset_ref")
        model_id = task.get("model_id")
        if model_id and spec.signal.type == "model" and spec.signal.model:
            spec.signal.model.model_id = model_id
            spec.signal.model.type = model_id if self._is_known_model(model_id) else spec.signal.model.type
        if dataset_ref and spec.signal.type == "model" and spec.signal.model:
            spec.signal.model.dataset_ref = dataset_ref

    @staticmethod
    def _is_known_model(model_id: str) -> bool:
        try:
            from app.models import get_registry
            return get_registry().get(model_id) is not None
        except Exception:
            return False

    @staticmethod
    def _allowed_imports() -> set[str]:
        try:
            from app.models import get_model_service
            return get_model_service().allowed_imports()
        except Exception:
            return set()

    # ---- 查询 -------------------------------------------------------------

    def get_task(self, task_id: str) -> dict[str, Any]:
        t = self.task_repo.get(task_id)
        if not t:
            raise KeyError(f"任务不存在: {task_id}")
        # 结果不塞进任务查询（单独接口），避免响应过大
        view = {k: v for k, v in t.items() if k not in ("result", "spec")}
        return view

    def get_result(self, task_id: str) -> dict[str, Any] | None:
        t = self.task_repo.get(task_id)
        if not t:
            raise KeyError(f"任务不存在: {task_id}")
        return t.get("result")

    def list_strategies(self, favorite_only: bool = False) -> list[dict[str, Any]]:
        # 列表不返回大体积 result
        return [
            {k: v for k, v in s.items() if k != "result"}
            for s in self.strategy_repo.list(favorite_only=favorite_only)
        ]

    def set_favorite(self, strategy_id: str, favorite: bool) -> None:
        self.strategy_repo.set_favorite(strategy_id, favorite)
