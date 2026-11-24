"""FastAPI application — implements docs/contracts.md.

Async submission: POST creates a task and schedules the synchronous pipeline in
a worker thread; clients poll GET /tasks/{id}. Configured via QP_* env vars.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

from app.api.deps import get_intent_service, get_task_service, get_track_service
from app.core.config import settings
from app.data.imports import ImportWorkflowError, get_import_service
from app.tracking import TrackError

API_PREFIX = "/api/v1"


# ---------- 请求模型 ----------

class StrategySubmit(BaseModel):
    # intent 与 frame 至少给一个：自由输入给 intent，引导式确认后给 frame
    intent: str = Field(default="", max_length=1000)
    structured_params: dict[str, Any] = Field(default_factory=dict)
    dataset_ref: str | None = None
    model_id: str | None = None
    client: dict[str, Any] = Field(default_factory=dict)
    frame: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _need_input(self) -> StrategySubmit:
        if not self.intent.strip() and not self.frame:
            raise ValueError("intent 与 frame 至少提供一个")
        return self


class IntentParseBody(BaseModel):
    """零代码入口：一句话或已选好的 frame，二选一。"""

    intent: str = Field(default="", max_length=1000)
    structured_params: dict[str, Any] = Field(default_factory=dict)
    frame: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _need_input(self) -> IntentParseBody:
        if not self.intent.strip() and not self.frame:
            raise ValueError("intent 与 frame 至少提供一个")
        return self


class FavoriteBody(BaseModel):
    favorite: bool = True


class ImportSubmitBody(BaseModel):
    submitter: str = ""


class ImportReviewBody(BaseModel):
    decision: Literal["approve", "reject"]
    reviewer: str = ""
    note: str = ""


# ---- 策略观察期（Track）----

class TrackCreateBody(BaseModel):
    """开一个观察：给回测完的 spec，或者给 strategy_id 让服务端自己捞。"""

    spec: dict[str, Any] | None = None
    strategy_id: str | None = None
    owner: str = "local"
    name: str | None = None
    horizon_days: int | None = Field(default=None, ge=1, le=250)
    rules: dict[str, Any] | None = None
    start_date: date | None = None
    backtest: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _need_target(self) -> TrackCreateBody:
        if not self.spec and not self.strategy_id:
            raise ValueError("spec 与 strategy_id 至少提供一个")
        return self


class TrackRunBody(BaseModel):
    trade_date: date | None = None


class TrackActionBody(BaseModel):
    note: str = ""
    extend_days: int = Field(default=0, ge=0, le=250)


def _track_error(exc: TrackError) -> None:
    raise HTTPException(exc.status_code, str(exc))


def _import_error(exc: ImportWorkflowError) -> None:
    raise HTTPException(exc.status_code, exc.message)


# ---------- app 工厂 ----------

def create_app() -> FastAPI:
    app = FastAPI(
        title="QuantPilot API",
        description="量航量化策略服务（模拟盘）。",
        version="0.1.0",
    )
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- 健康检查 ----
    @app.get(f"{API_PREFIX}/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": "0.1.0"}

    # ---- 零代码入口：理解意图 ----
    @app.post(f"{API_PREFIX}/intents/parse")
    def parse_intent(body: IntentParseBody) -> dict[str, Any]:
        """一句话 → 理解结果 + 中文参数卡 + 澄清问题。响应中不含表达式/代码。"""
        return get_intent_service().parse(
            body.intent,
            structured_params=body.structured_params,
            frame=body.frame,
        )

    # ---- 提交策略 ----
    @app.post(f"{API_PREFIX}/strategies", status_code=202)
    def submit_strategy(body: StrategySubmit, bg: BackgroundTasks) -> dict[str, Any]:
        svc = get_task_service()
        task = svc.submit(
            body.intent, body.structured_params,
            dataset_ref=body.dataset_ref, model_id=body.model_id,
            client=body.client, frame=body.frame,
        )
        bg.add_task(_run_pipeline, task["task_id"])
        return {
            "task_id": task["task_id"],
            "status": task["status"],
            "created_at": task["created_at"],
            "estimated_seconds": 90,
        }

    @app.get(f"{API_PREFIX}/tasks/{{task_id}}")
    def get_task(task_id: str) -> dict[str, Any]:
        svc = get_task_service()
        try:
            return svc.get_task(task_id)
        except KeyError:
            raise HTTPException(404, "任务不存在")

    @app.get(f"{API_PREFIX}/tasks/{{task_id}}/result")
    def get_result(task_id: str) -> dict[str, Any]:
        svc = get_task_service()
        try:
            task = svc.get_task(task_id)
            if task["status"] != "succeeded":
                raise HTTPException(409, f"任务尚未成功（状态={task['status']}）")
            result = svc.get_result(task_id)
            if result is None:
                raise HTTPException(404, "结果不存在")
            return result
        except KeyError:
            raise HTTPException(404, "任务不存在")

    @app.get(f"{API_PREFIX}/strategies")
    def list_strategies(
        favorite_only: bool = False,
        limit: int = Query(50, ge=1, le=200),
    ) -> dict[str, Any]:
        svc = get_task_service()
        items = svc.list_strategies(favorite_only=favorite_only)[:limit]
        return {"items": items, "total": len(items)}

    @app.post(f"{API_PREFIX}/strategies/{{strategy_id}}/favorite")
    def set_favorite(strategy_id: str, body: FavoriteBody) -> dict[str, Any]:
        svc = get_task_service()
        if svc.strategy_repo.get(strategy_id) is None:
            raise HTTPException(404, "策略不存在")
        svc.set_favorite(strategy_id, body.favorite)
        return {"id": strategy_id, "favorite": body.favorite}

    # ---- 策略观察期（Track）：回测之后每天跑的那条链路 ----
    @app.post(f"{API_PREFIX}/tracks", status_code=201)
    def create_track(body: TrackCreateBody) -> dict[str, Any]:
        """把一条策略放进观察期。spec / rules 从此冻结。"""
        spec = body.spec
        backtest = body.backtest
        if spec is None:
            strategy = get_task_service().strategy_repo.get(body.strategy_id or "")
            if strategy is None:
                raise HTTPException(404, "策略不存在")
            spec = strategy.get("spec")
            result = strategy.get("result") or {}
            if backtest is None and result.get("metrics"):
                backtest = {"metrics": result["metrics"]}
        try:
            return get_track_service().create_track(
                spec, owner=body.owner, strategy_id=body.strategy_id,
                name=body.name, horizon_days=body.horizon_days,
                rules=body.rules, start_date=body.start_date, backtest=backtest,
            )
        except TrackError as exc:
            _track_error(exc)

    @app.get(f"{API_PREFIX}/tracks")
    def list_tracks(
        owner: str | None = None,
        state: str | None = None,
        limit: int = Query(50, ge=1, le=200),
    ) -> dict[str, Any]:
        items = get_track_service().list_tracks(owner=owner, state=state, limit=limit)
        return {"items": items, "total": len(items)}

    @app.get(f"{API_PREFIX}/tracks/{{track_id}}")
    def get_track(track_id: str) -> dict[str, Any]:
        try:
            return get_track_service().get(track_id)
        except TrackError as exc:
            _track_error(exc)

    @app.get(f"{API_PREFIX}/tracks/{{track_id}}/journal")
    def track_journal(
        track_id: str, limit: int = Query(100, ge=1, le=500)
    ) -> dict[str, Any]:
        """时间轴日志：上线、每天一跑、成交受阻、检查点、失效线……"""
        try:
            items = get_track_service().journal(track_id, limit=limit)
            return {"items": items, "total": len(items)}
        except TrackError as exc:
            _track_error(exc)

    @app.get(f"{API_PREFIX}/tracks/{{track_id}}/nav")
    def track_nav(track_id: str) -> dict[str, Any]:
        try:
            items = get_track_service().navs(track_id)
            return {"items": items, "total": len(items)}
        except TrackError as exc:
            _track_error(exc)

    @app.get(f"{API_PREFIX}/tracks/{{track_id}}/positions")
    def track_positions(track_id: str) -> dict[str, Any]:
        try:
            items = get_track_service().positions(track_id)
            return {"items": items, "total": len(items)}
        except TrackError as exc:
            _track_error(exc)

    @app.post(f"{API_PREFIX}/tracks/{{track_id}}/run")
    def run_track_day(track_id: str, body: TrackRunBody = TrackRunBody()) -> dict[str, Any]:
        """手动"跑今天"。P1.6 先不做定时调度，靠这个按钮验证产品感觉。"""
        try:
            return get_track_service().run_day(track_id, body.trade_date)
        except TrackError as exc:
            _track_error(exc)

    @app.post(f"{API_PREFIX}/tracks/{{track_id}}/pause")
    def pause_track(track_id: str, body: TrackActionBody = TrackActionBody()) -> dict[str, Any]:
        try:
            return get_track_service().pause(track_id, body.note)
        except TrackError as exc:
            _track_error(exc)

    @app.post(f"{API_PREFIX}/tracks/{{track_id}}/resume")
    def resume_track(track_id: str, body: TrackActionBody = TrackActionBody()) -> dict[str, Any]:
        try:
            return get_track_service().resume(track_id, body.extend_days)
        except TrackError as exc:
            _track_error(exc)

    @app.post(f"{API_PREFIX}/tracks/{{track_id}}/abandon")
    def abandon_track(track_id: str, body: TrackActionBody = TrackActionBody()) -> dict[str, Any]:
        try:
            return get_track_service().abandon(track_id, body.note)
        except TrackError as exc:
            _track_error(exc)

    @app.post(f"{API_PREFIX}/tracks/{{track_id}}/archive")
    def archive_track(track_id: str, body: TrackActionBody = TrackActionBody()) -> dict[str, Any]:
        try:
            return get_track_service().archive(track_id, body.note)
        except TrackError as exc:
            _track_error(exc)

    @app.post(f"{API_PREFIX}/tracks/{{track_id}}/restore")
    def restore_track(track_id: str) -> dict[str, Any]:
        try:
            return get_track_service().restore(track_id)
        except TrackError as exc:
            _track_error(exc)

    # ---- 资产目录 ----
    @app.get(f"{API_PREFIX}/factors")
    def list_factors(
        tag: str | None = None,
        sort: str = "ir",
        limit: int = Query(50, ge=1, le=200),
    ) -> dict[str, Any]:
        from app.data import get_data_service
        svc = get_data_service()
        items = [f for f in svc.catalog.factors() if f.status == "active"]
        if tag:
            items = [f for f in items if tag in f.tags]
        if sort == "ir":
            items = sorted(items, key=lambda f: f.performance.ir or 0, reverse=True)
        data = [{
            "asset_id": f.asset_id, "name": f.name,
            "expression": f.expression, "direction": f.direction,
            "performance": f.performance.model_dump(mode="json"),
            "tags": f.tags,
        } for f in items[:limit]]
        return {"items": data, "total": len(data)}

    @app.get(f"{API_PREFIX}/datasets")
    def list_datasets() -> dict[str, Any]:
        from app.data import get_data_service
        svc = get_data_service()
        items = [{
            "asset_id": d.asset_id, "name": d.name, "description": d.description,
            "handler": d.handler, "tags": d.tags,
        } for d in svc.catalog.datasets()]
        return {"items": items, "total": len(items)}

    @app.get(f"{API_PREFIX}/models")
    def list_models(
        no_gpu: bool = True,
        sort: str = "ir",
    ) -> dict[str, Any]:
        from app.models import get_model_service
        svc = get_model_service()
        items = svc.list_models(no_gpu=no_gpu)
        if sort == "ir":
            items = sorted(items, key=lambda m: m.performance.ir or 0, reverse=True)
        data = [{
            "model_id": m.model_id, "name": m.name, "category": m.category,
            "status": m.status, "requires_gpu": m.requires_gpu,
            "performance": m.performance.model_dump(mode="json"),
            "description": m.description, "tags": m.tags,
        } for m in items]
        return {"items": data, "total": len(data)}

    # ---- 数据导入与审核 ----
    @app.post(f"{API_PREFIX}/data/imports", status_code=201)
    async def create_import(
        file: UploadFile = File(...),
        name: str | None = Form(default=None),
        owner: str = Form(default="user"),
        description: str = Form(default=""),
        kind: str = Form(default="csv_upload"),
        available_date_field: str | None = Form(default=None),
    ) -> dict[str, Any]:
        content = await file.read()
        try:
            return get_import_service().create_from_upload(
                filename=file.filename or "upload.csv",
                content=content,
                name=name,
                owner=owner,
                description=description,
                kind=kind,
                available_date_field=available_date_field,
            )
        except ImportWorkflowError as exc:
            _import_error(exc)

    @app.get(f"{API_PREFIX}/data/imports")
    def list_imports(
        status: str | None = None,
        owner: str | None = None,
        limit: int = Query(50, ge=1, le=200),
    ) -> dict[str, Any]:
        items = get_import_service().list_batches(status=status, owner=owner, limit=limit)
        return {"items": items, "total": len(items)}

    @app.get(f"{API_PREFIX}/data/imports/{{batch_id}}")
    def get_import(batch_id: str) -> dict[str, Any]:
        try:
            return get_import_service().get(batch_id)
        except ImportWorkflowError as exc:
            _import_error(exc)

    @app.get(f"{API_PREFIX}/data/imports/{{batch_id}}/preview")
    def preview_import(
        batch_id: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=200),
    ) -> dict[str, Any]:
        try:
            return get_import_service().preview(batch_id, offset=offset, limit=limit)
        except ImportWorkflowError as exc:
            _import_error(exc)

    @app.post(f"{API_PREFIX}/data/imports/{{batch_id}}/submit")
    def submit_import(batch_id: str, body: ImportSubmitBody = ImportSubmitBody()) -> dict[str, Any]:
        try:
            return get_import_service().submit(batch_id, submitter=body.submitter)
        except ImportWorkflowError as exc:
            _import_error(exc)

    @app.post(f"{API_PREFIX}/data/imports/{{batch_id}}/review")
    def review_import(batch_id: str, body: ImportReviewBody) -> dict[str, Any]:
        try:
            return get_import_service().review(
                batch_id, decision=body.decision, reviewer=body.reviewer, note=body.note
            )
        except ImportWorkflowError as exc:
            _import_error(exc)

    @app.post(f"{API_PREFIX}/data/imports/{{batch_id}}/publish")
    def publish_import(batch_id: str) -> dict[str, Any]:
        try:
            return get_import_service().publish(batch_id)
        except ImportWorkflowError as exc:
            _import_error(exc)

    return app


def _run_pipeline(task_id: str) -> None:
    """Background worker entry — get_task_service() holds the singleton state."""
    get_task_service().run(task_id)


app = create_app()
