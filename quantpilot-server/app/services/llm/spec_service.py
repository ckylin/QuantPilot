"""Stage A: natural-language intent -> validated Strategy Spec."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from pydantic import ValidationError

from app.core.config import settings
from app.schemas import StrategySpec
from app.services.llm.prompt_loader import PromptLoader, strip_markdown_fence


class SpecGenerationError(Exception):
    """Base error for Stage A."""


class SpecValidationError(SpecGenerationError):
    """Raised when the LLM cannot produce a schema-valid Spec."""

    def __init__(self, attempts: int, errors: list[str]):
        self.attempts = attempts
        self.errors = errors
        super().__init__(f"经过 {attempts} 次尝试仍未生成合法 Spec: {errors[-1] if errors else '未知错误'}")


class SpecGenerator:
    def __init__(self, provider: Any, prompts: PromptLoader):
        self.provider = provider
        self.prompts = prompts

    def generate(
        self,
        intent: str,
        structured_params: dict[str, Any] | None = None,
    ) -> tuple[StrategySpec, dict[str, Any]]:
        """Compile an intent into a validated Spec.

        Returns (spec, trace) where trace records attempts and prompt version.
        """
        this_year = date.today().year
        base_system = self.prompts.system(
            "intent2spec",
            intent=intent,
            structured_params=json.dumps(structured_params or {}, ensure_ascii=False),
            builtin_universes="csi300, csi500, csi1000, all",
            default_date_range=f"{this_year - 4}-01-01 ~ {this_year}-12-31",
        )

        system = base_system
        attempts = 0
        errors: list[str] = []

        for attempts in range(1, settings.spec_max_retries + 2):
            raw = self.provider.generate(
                system=system,
                user=intent,
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            try:
                data = json.loads(strip_markdown_fence(raw))
                spec = StrategySpec.model_validate(data)
                return spec, {"attempts": attempts, "prompt_version": "0.1"}
            except (json.JSONDecodeError, ValidationError) as exc:
                errors.append(f"第 {attempts} 次: {exc}")
                system = base_system + "\n\n【上次输出未通过校验，请修正以下问题】\n" + str(exc)

        raise SpecValidationError(attempts=attempts, errors=errors)
