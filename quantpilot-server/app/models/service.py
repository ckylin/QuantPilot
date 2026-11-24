"""ModelService — facade for model discovery, recommendation and code injection."""

from __future__ import annotations

from typing import Any

from app.models.registry import ModelRegistry, get_registry
from app.models.schemas import ModelCategory, ModelSpec


class ModelService:
    def __init__(self, registry: ModelRegistry | None = None):
        self.registry = registry or get_registry()

    def list_models(self, category: ModelCategory | None = None,
                    no_gpu: bool = True) -> list[ModelSpec]:
        items = self.registry.available()
        if category:
            items = [m for m in items if m.category == category]
        if no_gpu:
            items = [m for m in items if not m.requires_gpu]
        return items

    def recommend(self, intent: str = "", dataset_size: int | None = None,
                  no_gpu: bool = True, top: int = 3) -> list[ModelSpec]:
        return self.registry.recommend(intent, dataset_size, no_gpu, top)

    def auto_select(self, intent: str = "", dataset_size: int | None = None,
                    no_gpu: bool = True) -> ModelSpec:
        """自动选模：返回推荐榜首，无候选时回退 lgbm。"""
        recs = self.recommend(intent, dataset_size, no_gpu, top=1)
        if recs:
            return recs[0]
        fallback = self.registry.get("lgbm")
        if fallback:
            return fallback
        raise RuntimeError("模型注册表为空，无法选模")

    def prompt_catalog(self, no_gpu: bool = True) -> str:
        return self.registry.prompt_catalog(no_gpu)

    def code_snippet(self, model_id: str, user_params: dict[str, Any] | None = None) -> dict[str, Any]:
        """产出注入 GENERATED_BLOCK 的代码片段。

        返回 imports 与 constructor 表达式。模板/守卫都不需要认识具体模型。
        """
        m = self.registry.require(model_id)
        if m.status == "deprecated":
            raise ValueError(f"模型已废弃: {model_id}")
        return {
            "imports": list(m.imports),
            "constructor": m.build_constructor(user_params),
            "model_id": model_id,
            "category": m.category,
        }

    def allowed_imports(self) -> set[str]:
        return self.registry.allowed_imports()

    def required_packages(self, model_ids: list[str]) -> set[str]:
        return self.registry.required_pip_packages(model_ids)


_service: ModelService | None = None


def get_model_service() -> ModelService:
    global _service
    if _service is None:
        _service = ModelService()
    return _service


def set_model_service(service: ModelService) -> None:
    global _service
    _service = service
