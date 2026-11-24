"""Stage B: validated Strategy Spec -> qlib code (template filling)."""

from __future__ import annotations

import json
import pprint
from typing import Any

from app.core.config import settings
from app.schemas import StrategySpec
from app.services.llm.ast_guard import GENERATED_END, GENERATED_START, check_code
from app.services.llm.prompt_loader import PromptLoader, strip_markdown_fence


class CodeGenerationError(Exception):
    """Base error for Stage B."""


class CodeRejectedError(CodeGenerationError):
    """Raised when generated code fails the AST / fixed-region check."""

    def __init__(self, attempts: int, errors: list[str]):
        self.attempts = attempts
        self.errors = errors
        super().__init__(f"经过 {attempts} 次尝试生成的代码仍未通过检查: {errors[-1] if errors else '未知错误'}")


class CodeGenerator:
    def __init__(self, provider: Any, prompts: PromptLoader):
        self.provider = provider
        self.prompts = prompts

    def _prompt_variables(self, spec: StrategySpec) -> dict[str, str]:
        return {
            "spec_json": json.dumps(spec.model_dump(mode="json"), ensure_ascii=False, indent=2),
            "code_template": self.prompts.code_template(spec.signal.type),
        }

    def generate(self, spec: StrategySpec) -> tuple[str, dict[str, Any]]:
        template = self.prompts.code_template(spec.signal.type)
        base_system = self.prompts.system("spec2code", **self._prompt_variables(spec))
        # AST 白名单从注册表动态派生：新增模型无需手改守卫
        from app.models import get_model_service
        extra_imports = get_model_service().allowed_imports()

        system = base_system
        attempts = 0
        errors: list[str] = []

        for attempts in range(1, settings.code_max_retries + 2):
            raw = self.provider.generate(
                system=system,
                user="请严格按系统提示填充模板并输出完整 Python 文件。",
                temperature=0.0,
            )
            code = strip_markdown_fence(raw)
            passed, errors = check_code(code, template, extra_imports=extra_imports)
            if passed:
                return code, {"attempts": attempts, "prompt_version": "0.1"}
            system = base_system + "\n\n【AST 检查未通过，请修正以下问题】\n" + "\n".join(errors)

        raise CodeRejectedError(attempts=attempts, errors=errors)

    def render_deterministic(self, spec: StrategySpec) -> str:
        """Render the template directly from the Spec without calling the LLM.

        Useful for Phase 0 sandbox-chain validation and as a fallback when the
        LLM is unavailable. The fixed runner section is left byte-identical.
        """
        template = self.prompts.code_template(spec.signal.type)
        start = template.index(GENERATED_START)
        end = template.index(GENERATED_END)
        head, block, tail = template[:start], template[start:end], template[end:]

        # pformat emits a valid Python literal (None/True/False), whereas
        # json.dumps would write null/true/false and break Python syntax.
        values = {
            "SPEC_DICT": pprint.pformat(spec.model_dump(mode="json"), indent=2, sort_dicts=False),
        }
        if spec.signal.type == "model":
            # 模型构造代码来自 ModelRegistry —— 加新模型无需改模板/AST/镜像。
            from app.models import get_model_service
            model_cfg = spec.signal.model
            model_id = (model_cfg.model_id or model_cfg.type)
            snippet = get_model_service().code_snippet(
                model_id, user_params=model_cfg.params
            )
            values.update({
                "MODEL_IMPORTS": "\n".join(snippet["imports"]),
                "MODEL_CONSTRUCTOR": snippet["constructor"],
            })
        if spec.signal.type == "expression":
            filters = [f.model_dump() for f in spec.filters]
            values.update({
                "EXPRESSION": repr(spec.signal.expression),
                "FILTERS": repr(filters),
                "STRATEGY_KWARGS": repr(
                    {
                        "topk": spec.portfolio.topk,
                        "n_drop": spec.effective_n_drop,
                        "signal_direction": spec.signal.direction,
                        "filters": filters,
                    }
                ),
            })
        for key, value in values.items():
            block = block.replace("{{" + key + "}}", value)
        return head + block + tail
