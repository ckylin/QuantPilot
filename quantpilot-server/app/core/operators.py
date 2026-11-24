"""qlib expression validation against the operator whitelist.

The whitelist lives in ``prompts/operators.yaml`` (single source of truth) and
is consumed both here (runtime validation) and by the prompt loader (rendered
into the Stage A system prompt).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from app.core.config import settings

# $field or bare identifier. A bare identifier on its own is always an operator.
_TOKEN_RE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*|[A-Za-z_][A-Za-z0-9_]*")
_STRING_RE = re.compile(r"['\"][^'\"]*['\"]")


@lru_cache(maxsize=1)
def load_operators() -> dict:
    """Load and cache the operator whitelist."""
    path = Path(settings.prompts_dir) / "operators.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def extract_identifiers(expr: str) -> list[str]:
    """Extract all field/operator identifiers, ignoring string literals."""
    return _TOKEN_RE.findall(_STRING_RE.sub(" ", expr))


def validate_expression(expr: str) -> list[str]:
    """Return a list of validation errors; empty list means valid.

    Strategy: every identifier must be in the whitelist (field for ``$x``,
    operator otherwise). This also rejects eval/open/os/etc. without needing
    substring matching, which would wrongly reject the legitimate ``$open``
    field.
    """
    cfg = load_operators()
    fields = set(cfg.get("fields", []))
    operators = set(cfg.get("unary", [])) | set(cfg.get("binary", []))
    errors: list[str] = []

    if _STRING_RE.search(expr):
        errors.append("字符串字面量不允许出现在表达式中")

    for token in extract_identifiers(expr):
        if token.startswith("$"):
            if token not in fields:
                errors.append(f"未知字段: {token}")
        elif token not in operators:
            errors.append(f"未知算子/标识符: {token}")

    return errors


def operator_hint() -> str:
    """Render a compact whitelist summary for injection into prompts."""
    cfg = load_operators()
    return (
        "字段：" + "、".join(cfg["fields"]) + "\n"
        "算子：" + "、".join(cfg["unary"] + cfg["binary"]) + "\n"
        "算术：" + " ".join(cfg["arithmetic"])
    )


def field_hint() -> str:
    """Render only the field list, for prompt variables that need it."""
    return "、".join(load_operators()["fields"])
