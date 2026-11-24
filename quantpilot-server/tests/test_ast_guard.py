"""Tests for AST static checking and fixed-region integrity."""

from __future__ import annotations

import pytest

from app.schemas import StrategySpec
from app.services.llm.ast_guard import check_code, check_fixed_region
from app.services.llm.codegen_service import CodeGenerator
from app.services.llm.prompt_loader import PromptLoader


@pytest.fixture
def template() -> str:
    return PromptLoader().code_template()


@pytest.fixture
def valid_code(base_spec) -> str:
    spec = StrategySpec.model_validate(base_spec)
    return CodeGenerator(None, PromptLoader()).render_deterministic(spec)


def test_template_renders_valid_python(valid_code, template):
    passed, errors = check_code(valid_code, template)
    assert passed, errors
    assert check_fixed_region(valid_code, template) == []


def test_reject_forbidden_import(valid_code, template):
    bad = valid_code.replace("import math", "import os\nimport math", 1)
    passed, errors = check_code(bad, template)
    assert not passed
    assert any("禁止的 import" in e for e in errors)


def test_reject_eval_call(valid_code, template):
    bad = valid_code.replace("EXPRESSION =", "EXPRESSION = eval('1+1')\nEXPRESSION =", 1)
    passed, errors = check_code(bad, template)
    assert not passed
    assert any("禁止的调用" in e for e in errors)


def test_reject_modified_fixed_region(valid_code, template):
    bad = valid_code.replace("def run():", "def run_evil():", 1)
    passed, errors = check_code(bad, template)
    assert not passed
    assert any("固定区被修改" in e for e in errors)


def test_reject_syntax_error(valid_code, template):
    bad = valid_code.replace("def run():", "def run(:\n", 1)
    passed, errors = check_code(bad, template)
    assert not passed
    assert any("语法错误" in e for e in errors)


def test_reject_dangerous_string_in_generated_block(valid_code, template):
    bad = valid_code.replace("STRATEGY_KWARGS =", "NOTE = 'import subprocess'\nSTRATEGY_KWARGS =", 1)
    passed, errors = check_code(bad, template)
    assert not passed
    assert any("禁止内容" in e for e in errors)
