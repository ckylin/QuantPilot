"""Execute the generated runner against a fake qlib module.

This validates that the fixed runner section in code_template.py actually
produces a well-formed QUANTPLOT_RESULT payload, without needing real qlib
data or a container.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from app.schemas import StrategySpec
from app.services.llm.codegen_service import CodeGenerator
from app.services.llm.prompt_loader import PromptLoader

FAKE_QLIB = r'''
import sys, types, pandas as pd, numpy as np

# --- fake qlib package tree ---
qlib = types.ModuleType("qlib")
qlib.__version__ = "fake"
def init(**kwargs): pass
qlib.init = init
sys.modules["qlib"] = qlib

constant = types.ModuleType("qlib.constant"); constant.REG_CN = "cn"
sys.modules["qlib.constant"] = constant

data = types.ModuleType("qlib.data")
D = types.ModuleType("qlib.data.D")
dates = pd.date_range("2020-01-01", periods=60, freq="B")
symbols = ["SH600000", "SH600001", "SH600002"]
idx = pd.MultiIndex.from_product([symbols, dates], names=["instrument", "datetime"])
def features(instruments, fields, start_time, end_time, freq="day"):
    rng = np.random.default_rng(42)
    values = rng.uniform(10, 100, size=len(idx))
    return pd.DataFrame({fields[0]: values}, index=idx)
def instruments(name): return symbols
D.features = features; D.instruments = instruments
data.D = D
sys.modules["qlib.data"] = data
sys.modules["qlib.data.D"] = D

strategy_mod = types.ModuleType("qlib.contrib.strategy")
class TopkDropoutStrategy:
    def __init__(self, signal, topk, n_drop): pass
strategy_mod.TopkDropoutStrategy = TopkDropoutStrategy
sys.modules["qlib.contrib.strategy"] = strategy_mod

eval_mod = types.ModuleType("qlib.contrib.evaluate")
def backtest_daily(start_time, end_time, strategy, **kwargs):
    r = pd.Series(np.random.default_rng(1).uniform(-0.02, 0.02, size=60), index=dates)
    report = pd.DataFrame({
        "return": r,
        "turnover": pd.Series([0.1] * 60, index=dates),
        "bench": r * 0.8,
    })
    return report, None
def risk_analysis(returns):
    # qlib 0.9.7 returns a DataFrame indexed by metric name with column "risk".
    return pd.DataFrame({"risk": {
        "annualized_return": 0.18,
        "information_ratio": 1.4,
        "max_drawdown": -0.12,
        "calmar": 1.5,
    }})
eval_mod.backtest_daily = backtest_daily
eval_mod.risk_analysis = risk_analysis
sys.modules["qlib.contrib.evaluate"] = eval_mod
'''


def test_runner_produces_standardized_result(base_spec):
    pandas = pytest.importorskip("pandas")
    numpy = pytest.importorskip("numpy")

    spec = StrategySpec.model_validate(base_spec)
    code = CodeGenerator(None, PromptLoader()).render_deterministic(spec)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "fake_qlib.py").write_text(FAKE_QLIB, encoding="utf-8")
        (tmp_path / "solution.py").write_text(code, encoding="utf-8")

        proc = subprocess.run(
            [sys.executable, "-c", f"import fake_qlib; exec(open('solution.py', encoding='utf-8').read())"],
            cwd=str(tmp_path), capture_output=True, text=True, timeout=30,
        )

    assert proc.returncode == 0, f"stderr:\n{proc.stderr}"
    marker = "QUANTPLOT_RESULT:"
    result_line = next((line for line in proc.stdout.splitlines() if line.startswith(marker)), None)
    assert result_line is not None, f"未找到结果标记，stdout:\n{proc.stdout}"

    payload = json.loads(result_line[len(marker):])
    assert payload["status"] == "done"
    assert payload["metrics"]["annual_return"] == 0.18
    assert payload["metrics"]["sharpe"] == 1.4
    assert payload["metrics"]["max_drawdown"] == -0.12
    assert len(payload["series"]["nav"]) > 0
    assert payload["spec"]["spec_version"] == "0.1"
