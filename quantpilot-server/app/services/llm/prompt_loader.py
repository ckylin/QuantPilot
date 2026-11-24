"""Load and render prompt templates from ``prompts/``."""

from __future__ import annotations

import json
from pathlib import Path

from app.core.config import settings
from app.core.operators import field_hint, operator_hint


class PromptLoader:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else Path(settings.prompts_dir)

    @staticmethod
    def render(template: str, **variables: str) -> str:
        result = template
        for key, value in variables.items():
            result = result.replace("{{" + key + "}}", str(value))
        return result

    def _read(self, relative: str) -> str:
        """Read a prompt file, stripping optional YAML front-matter."""
        text = (self.root / relative).read_text(encoding="utf-8")
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) == 3:
                text = parts[2].lstrip("\n")
        return text

    def system(self, stage: str, **variables: str) -> str:
        template = self._read(f"{stage}/system.md")
        defaults = {
            "operator_hint": operator_hint(),
            "fields": field_hint(),
            "builtin_universes": "csi300, csi500, csi1000, all",
            "fewshot_examples": self.fewshot(stage),
            "factor_catalog": self._factor_catalog_text(),
            "model_catalog": self._model_catalog_text(),
        }
        defaults.update(variables)
        return self.render(template, **defaults)

    @staticmethod
    def _model_catalog_text() -> str:
        """从 Model Zoo 拉取模型目录注入 prompt；模块不可用时静默降级。"""
        try:
            from app.models import get_model_service
            text = get_model_service().prompt_catalog(no_gpu=True)
            return text if text.strip() else "（暂无预置模型）"
        except Exception:
            return "（模型目录暂不可用）"

    @staticmethod
    def _factor_catalog_text() -> str:
        """从数据工坊拉取因子目录注入 prompt；模块不可用时静默降级。

        惰性 import + 容错，保证 prompt 渲染永不因数据模块问题失败。
        """
        try:
            from app.data import get_data_service, seed_catalog
            svc = get_data_service()
            if not svc.catalog.factors():
                seed_catalog(svc)
            text = svc.factor_catalog_prompt()
            return text if text.strip() else "（暂无预置因子）"
        except Exception:
            return "（因子目录暂不可用）"

    def fewshot(self, stage: str) -> str:
        path = self.root / stage / "fewshot.jsonl"
        if not path.exists():
            return ""
        return "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if line.strip())

    def code_template(self, signal_type: str = "expression") -> str:
        """Return the fixed runner template for the given signal type.

        expression -> 因子表达式打分 + 横截面排序（轻量、无训练）
        model      -> qlib 原生 workflow：Alpha158 -> LGBModel -> pred -> 组合回测
        """
        filename = (
            "model_workflow_template.py"
            if signal_type == "model"
            else "code_template.py"
        )
        return (self.root / "spec2code" / filename).read_text(encoding="utf-8")


def strip_markdown_fence(text: str) -> str:
    """Remove ```json / ```python wrappers if the model emitted them."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()
