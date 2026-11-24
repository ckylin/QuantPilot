"""End-to-end chain tests with MockProvider (no network / no qlib needed)."""

from __future__ import annotations

import json

from app.schemas import StrategySpec
from app.services.llm.ast_guard import check_code
from app.services.llm.codegen_service import CodeGenerator
from app.services.llm.prompt_loader import PromptLoader
from app.services.llm.provider import MockProvider
from app.services.llm.spec_service import SpecGenerator


def test_stage_a_intent_to_spec(base_spec):
    provider = MockProvider(spec_response=base_spec)
    spec, trace = SpecGenerator(provider, PromptLoader()).generate(
        "沪深300里动量最强的20只股票，每月调仓"
    )
    assert isinstance(spec, StrategySpec)
    assert spec.universe.name == "csi300"
    assert trace["attempts"] == 1


def test_stage_b_deterministic_render(base_spec):
    spec = StrategySpec.model_validate(base_spec)
    generator = CodeGenerator(None, PromptLoader())
    code = generator.render_deterministic(spec)

    passed, errors = check_code(code, PromptLoader().code_template())
    assert passed, errors
    assert "SPEC = {" in code
    assert "$close / Ref($close, 20) - 1" in code
    assert "QUANTPLOT_RESULT" in code
    # placeholders for the executor layer must be preserved
    assert "{{TASK_ID}}" in code and "{{LOGS_REF}}" in code


def test_stage_b_llm_path_with_mock(base_spec):
    spec = StrategySpec.model_validate(base_spec)
    prompts = PromptLoader()
    code = CodeGenerator(None, prompts).render_deterministic(spec)

    provider = MockProvider(code_response=code)
    generated, trace = CodeGenerator(provider, prompts).generate(spec)
    assert generated.strip() == code.strip()
    assert trace["attempts"] == 1


def test_full_chain(base_spec):
    provider = MockProvider(spec_response=base_spec)
    prompts = PromptLoader()

    spec, _ = SpecGenerator(provider, prompts).generate(base_spec["meta"]["intent"])
    code = CodeGenerator(None, prompts).render_deterministic(spec)
    provider.code_response = code
    final_code, _ = CodeGenerator(provider, prompts).generate(spec)

    passed, errors = check_code(final_code, prompts.code_template())
    assert passed, errors

    # Verify the generated code's SPEC block is the exact same spec.
    namespace = {}
    lines = final_code.splitlines()
    start = next(i for i, line in enumerate(lines) if "GENERATED_BLOCK (START)" in line)
    end = next(i for i, line in enumerate(lines) if "GENERATED_BLOCK (END)" in line)
    block = "\n".join(lines[start + 1 : end])
    exec(block, namespace)  # noqa: S102 - block is AST-checked test fixture
    assert namespace["SPEC"]["spec_version"] == "0.1"
    assert namespace["STRATEGY_KWARGS"]["topk"] == 20
    assert namespace["STRATEGY_KWARGS"]["n_drop"] == 4
    assert namespace["EXPRESSION"] == "$close / Ref($close, 20) - 1"


def test_prompt_rendering_includes_fewshot_and_operators(base_spec):
    prompts = PromptLoader()
    system = prompts.system("intent2spec", intent="测试意图", structured_params="{}")
    assert "测试意图" in system
    assert "$close" in system
    assert "Ref" in system
    assert "沪深300动量Top20" in system  # from fewshot
