"""Tests for the sandbox executor (no podman / no qlib required)."""

from __future__ import annotations

import json

from app.services.executor.sandbox import RESULT_MARKER, ExecutionResult, SandboxExecutor


def _valid_result_payload() -> dict:
    return {
        "status": "done",
        "metrics": {"annual_return": 0.18, "sharpe": 1.4, "max_drawdown": -0.12},
        "series": {"nav": [{"date": "2020-01-02", "value": 1.0}]},
    }


def test_parse_success():
    payload = _valid_result_payload()
    stdout = f"[qlib] loading data...\n{RESULT_MARKER}{json.dumps(payload)}\n"

    def fake(script, task_id, timeout):
        return stdout, "", 0, False

    executor = SandboxExecutor(runner="fake", fake_runner=fake)
    result = executor.execute("print('x')", task_id="abc123")

    assert isinstance(result, ExecutionResult)
    assert result.success
    assert result.result is not None
    assert result.result["metrics"]["sharpe"] == 1.4
    assert result.result["task_id"] == "abc123"
    assert result.errors == []


def test_parse_missing_marker():
    def fake(script, task_id, timeout):
        return "no result here\n", "", 0, False

    result = SandboxExecutor(runner="fake", fake_runner=fake).execute("x", task_id="t1")
    assert not result.success
    assert any("未找到" in e for e in result.errors)


def test_parse_malformed_json():
    def fake(script, task_id, timeout):
        return f"{RESULT_MARKER}{{not json\n", "", 0, False

    result = SandboxExecutor(runner="fake", fake_runner=fake).execute("x", task_id="t2")
    assert not result.success
    assert any("解析失败" in e for e in result.errors)


def test_parse_bad_structure():
    def fake(script, task_id, timeout):
        return f"{RESULT_MARKER}{{\"foo\": 1}}\n", "", 0, False

    result = SandboxExecutor(runner="fake", fake_runner=fake).execute("x", task_id="t3")
    assert not result.success
    assert any("结构不合法" in e for e in result.errors)


def test_parse_timeout_and_error_exit():
    payload = _valid_result_payload()

    def fake(script, task_id, timeout):
        return f"{RESULT_MARKER}{json.dumps(payload)}\n", "killed", -1, True

    result = SandboxExecutor(runner="fake", fake_runner=fake).execute("x", task_id="t4")
    assert not result.success
    assert result.timed_out
    assert any("超时" in e for e in result.errors)
    assert any("退出码" in e for e in result.errors)


def test_fake_receives_written_script():
    captured = {}

    def fake(script, task_id, timeout):
        captured["code"] = open(script, encoding="utf-8").read()
        captured["task_id"] = task_id
        captured["timeout"] = timeout
        return f"{RESULT_MARKER}{json.dumps(_valid_result_payload())}", "", 0, False

    executor = SandboxExecutor(runner="fake", fake_runner=fake, timeout_sec=45)
    result = executor.execute("print('hello')", task_id="t5")

    assert result.success
    assert captured["code"] == "print('hello')"
    assert captured["task_id"] == "t5"
    assert captured["timeout"] == 45


def test_temp_dir_cleaned_up():
    import glob
    import tempfile

    before = set(glob.glob(str(__import__("pathlib").Path(tempfile.gettempdir()) / "qp-task-*")))

    def fake(script, task_id, timeout):
        return f"{RESULT_MARKER}{json.dumps(_valid_result_payload())}", "", 0, False

    SandboxExecutor(runner="fake", fake_runner=fake).execute("x", task_id="cleanup-test")
    after = set(glob.glob(str(__import__("pathlib").Path(tempfile.gettempdir()) / "qp-task-*")))
    assert after == before, "临时工作目录未被清理"


def test_placeholders_injected_before_run():
    captured = {}

    def fake(script, task_id, timeout):
        captured["code"] = open(script, encoding="utf-8").read()
        return f"{RESULT_MARKER}{json.dumps(_valid_result_payload())}", "", 0, False

    code = 'TASK = "{{TASK_ID}}"\nLOG = "{{LOGS_REF}}"'
    SandboxExecutor(runner="fake", fake_runner=fake).execute(code, task_id="inject-99")
    assert "{{TASK_ID}}" not in captured["code"]
    assert "{{LOGS_REF}}" not in captured["code"]
    assert 'inject-99' in captured["code"]
    assert "minio://logs/inject-99.log" in captured["code"]


def test_result_task_id_cannot_be_forged():
    payload = _valid_result_payload()
    payload["task_id"] = "forged-id"

    def fake(script, task_id, timeout):
        return f"{RESULT_MARKER}{json.dumps(payload)}", "", 0, False

    result = SandboxExecutor(runner="fake", fake_runner=fake).execute("x", task_id="real-id")
    assert result.result["task_id"] == "real-id"


def test_invalid_runner_rejected():
    try:
        SandboxExecutor(runner="unknown")
    except ValueError:
        pass
    else:
        raise AssertionError("未知 runner 应被拒绝")
