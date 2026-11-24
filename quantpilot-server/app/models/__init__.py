"""QuantPilot Model Zoo — pluggable prediction models.

A model is a first-class asset (like factors in DataFoundry): declare it in the
registry once, and it becomes generatable, AST-allowed and recommendable with
no changes to the code template, AST guard or sandbox image.
"""

from app.models.evaluation import Benchmark, PromotionRule
from app.models.registry import ModelRegistry, get_registry, seed_models, set_registry
from app.models.schemas import ModelPerformance, ModelSpec
from app.models.service import ModelService, get_model_service, set_model_service

__all__ = [
    "ModelRegistry",
    "get_registry",
    "set_registry",
    "seed_models",
    "ModelSpec",
    "ModelPerformance",
    "ModelService",
    "get_model_service",
    "set_model_service",
    "Benchmark",
    "PromotionRule",
]
