"""Generate 10 solution.py files for container-based acceptance testing.

Usage:
    python tests/gen_acceptance_solutions.py [output_dir]

Default output: sandbox/work/acceptance/
Each case produces <ID>_solution.py that the container will run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make imports work when run from quantpilot-server/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.schemas import StrategySpec
from app.services.llm.ast_guard import check_code
from app.services.llm.codegen_service import CodeGenerator
from app.services.llm.prompt_loader import PromptLoader
from phase0_cases import CASES


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("sandbox/work/acceptance")
    out_dir.mkdir(parents=True, exist_ok=True)

    prompts = PromptLoader()
    generator = CodeGenerator(None, prompts)
    template = prompts.code_template()

    manifest: list[dict] = []
    failures = 0
    for case in CASES:
        case_id = case["id"]
        try:
            spec = StrategySpec.model_validate(case["spec"])
            code = generator.render_deterministic(spec)
            passed, errors = check_code(code, template)
            if not passed:
                print(f"[FAIL] {case_id}: AST check failed: {errors}")
                failures += 1
                continue
            solution_path = out_dir / f"{case_id}_solution.py"
            solution_path.write_text(code, encoding="utf-8")
            manifest.append({
                "id": case_id,
                "intent": case["intent"],
                "solution": solution_path.name,
                "expression": spec.signal.expression,
                "topk": spec.portfolio.topk,
                "direction": spec.signal.direction,
                "universe": spec.universe.name or "custom",
            })
            print(f"[OK]   {case_id}: {solution_path.name}")
        except Exception as exc:
            print(f"[FAIL] {case_id}: {exc}")
            failures += 1

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n生成 {len(manifest)}/{len(CASES)} 个 solution 到 {out_dir}")
    print(f"manifest: {manifest_path}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
