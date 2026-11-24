"""AST static check for LLM-generated code.

Two layers of defence:
1. Syntax/AST whitelist: allowed imports, forbidden calls and dunder access.
2. Fixed-region integrity: only the GENERATED_BLOCK may differ from the
   template; the whole runner section must be byte-for-byte identical.

String-literal checks are scoped to the GENERATED_BLOCK only and use explicit
danger patterns (short substrings like "os" would wrongly reject "cost").
"""

from __future__ import annotations

import ast

GENERATED_START = "# ============ GENERATED_BLOCK (START)"
GENERATED_END = "# ============ GENERATED_BLOCK (END)"

ALLOWED_IMPORTS = {
    # 标准库
    "json",
    "math",
    "pandas",
    # qlib 基础（表达式模板）
    "qlib",
    "qlib.constant",
    "qlib.data",
    "qlib.contrib.strategy",
    "qlib.backtest",
    "qlib.contrib.evaluate",
    # qlib 预测模型 workflow（模型模板）
    "qlib.data.dataset",
    "qlib.data.dataset.handler",
    "qlib.contrib.data.handler",
    "qlib.contrib.model.gbdt",
}
FORBIDDEN_CALLS = {
    "eval", "exec", "open", "__import__", "compile",
    "globals", "locals", "input", "getattr", "setattr",
}
DANGEROUS_STRING_PATTERNS = (
    "subprocess", "os.system", "socket.", "requests.",
    "eval(", "exec(", "import ", "__import__",
)


def _split_regions(code: str) -> tuple[str, str, str] | None:
    if GENERATED_START not in code or GENERATED_END not in code:
        return None
    head, rest = code.split(GENERATED_START, 1)
    block, tail = rest.split(GENERATED_END, 1)
    return head, block, tail


def _block_line_range(code: str) -> tuple[int, int]:
    lines = code.splitlines()
    start = end = 0
    for i, line in enumerate(lines, start=1):
        if GENERATED_START in line:
            start = i
        elif GENERATED_END in line:
            end = i
            break
    return start, end


def check_fixed_region(code: str, template: str) -> list[str]:
    errors: list[str] = []
    parts_code = _split_regions(code)
    parts_tpl = _split_regions(template)
    if parts_code is None:
        return ["生成代码缺少 GENERATED_BLOCK 标记"]
    if parts_tpl is None:
        return ["代码模板缺少 GENERATED_BLOCK 标记"]
    # Compare with surrounding whitespace stripped: strip_markdown_fence() may
    # trim leading/trailing blank lines, which does not change code semantics.
    if parts_code[0].strip() != parts_tpl[0].strip():
        errors.append("GENERATED_BLOCK 之前的固定区被修改")
    if parts_code[2].strip() != parts_tpl[2].strip():
        errors.append("GENERATED_BLOCK 之后的固定区被修改")
    return errors


def check_code(code: str, template: str,
               extra_imports: set[str] | None = None) -> tuple[bool, list[str]]:
    """Return (passed, errors).

    extra_imports: additional allowed import modules (e.g. from ModelRegistry,
    so newly registered models don't require editing this whitelist).
    """
    errors: list[str] = []
    allowed = ALLOWED_IMPORTS | (extra_imports or set())
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, [f"语法错误: {exc}"]

    block_start, block_end = _block_line_range(code)

    for node in ast.walk(tree):
        lineno = getattr(node, "lineno", None)
        in_block = lineno is not None and block_start <= lineno <= block_end

        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in allowed:
                    errors.append(f"禁止的 import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module not in allowed:
                errors.append(f"禁止的 from-import: {module}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CALLS:
                errors.append(f"禁止的调用: {node.func.id}")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            errors.append(f"禁止访问属性: {node.attr}")
        elif in_block and isinstance(node, ast.Constant) and isinstance(node.value, str):
            lowered = node.value.lower()
            for pattern in DANGEROUS_STRING_PATTERNS:
                if pattern in lowered:
                    errors.append(f"生成区字符串包含禁止内容: {pattern}")
                    break

    errors.extend(check_fixed_region(code, template))
    return not errors, errors
