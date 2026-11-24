"""Tests for the offline demo runner (API demos without qlib/podman)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.executor.sandbox import RESULT_MARKER, SandboxExecutor


def test_demo_runner_emits_standard_result():
    ex = SandboxExecutor(runner="demo")
    result = ex.execute("print('ignored')", task_id="demo-001")
    assert result.success, result.errors
    r = result.result
    assert r["task_id"] == "demo-001"
    assert r["status"] == "done"
    assert len(r["series"]["nav"]) > 0
    assert len(r["series"]["benchmark_nav"]) == len(r["series"]["nav"])
    assert len(r["series"]["drawdown"]) == len(r["series"]["nav"])
    assert r["metrics"]["sharpe"] > 0
    assert r["metrics"]["max_drawdown"] <= 0


def test_demo_runner_metrics_realistic():
    ex = SandboxExecutor(runner="demo")
    r = ex.execute("", task_id="demo-002").result
    m = r["metrics"]
    # demo 指标应落在可信区间（不夸大）
    assert -0.2 <= m["annual_return"] <= 0.4
    assert 0.5 <= m["sharpe"] <= 3.0
    assert -0.3 <= m["max_drawdown"] <= 0


def test_demo_runner_ignores_code_content():
    """demo runner 不执行代码，任何代码字符串都产出相同结构。"""
    ex = SandboxExecutor(runner="demo")
    a = ex.execute("x=1", task_id="t").result
    b = ex.execute("import os; os.system('bad')", task_id="t").result
    assert a["metrics"] == b["metrics"]
