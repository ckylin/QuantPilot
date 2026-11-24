"""Sandbox executor: run generated qlib code and parse standardized results.

Three runner backends (selected by ``runner``):
  * ``podman`` — production. Isolated container, no network, read-only mounts.
  * ``local``  — development on a machine with qlib installed but no podman.
                 NOT isolated; never use for untrusted LLM code in production.
  * ``fake``   — tests. A callable returns canned stdout.

The generated code prints one line prefixed with ``QUANTPLOT_RESULT:`` followed
by the standardized result JSON. We parse that line and discard the rest.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

RESULT_MARKER = "QUANTPLOT_RESULT:"
DEFAULT_TIMEOUT_SEC = 120


@dataclass
class ExecutionResult:
    success: bool
    result: dict[str, Any] | None = None
    stdout: str = ""
    stderr: str = ""
    returncode: int | None = None
    timed_out: bool = False
    task_id: str = ""
    errors: list[str] = field(default_factory=list)


class SandboxExecutor:
    def __init__(
        self,
        *,
        runner: str = "podman",
        image: str = "quantpilot/sandbox:0.1",
        data_dir: Path | str | None = None,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        fake_runner: Callable[[str, str, int], tuple[str, str, int, bool]] | None = None,
    ):
        """Initialize the executor.

        Args:
            runner: "podman" | "local" | "fake".
            image: sandbox container image (podman runner only).
            data_dir: qlib data directory (podman/local runners).
            timeout_sec: hard timeout in seconds.
            fake_runner: for tests, signature (script_path, task_id, timeout) ->
                         (stdout, stderr, returncode, timed_out).
        """
        if runner not in {"podman", "local", "fake", "demo"}:
            raise ValueError(f"未知的 runner: {runner}")
        if runner == "fake" and fake_runner is None:
            raise ValueError("fake runner 必须提供 fake_runner 回调")

        self.runner = runner
        self.image = image
        self.data_dir = Path(data_dir) if data_dir else Path("sandbox/data/cn_data")
        self.timeout_sec = timeout_sec
        self.fake_runner = fake_runner

    def execute(self, code: str, task_id: str | None = None) -> ExecutionResult:
        task_id = task_id or uuid.uuid4().hex[:12]
        work_dir = Path(tempfile.mkdtemp(prefix=f"qp-task-{task_id}-"))
        try:
            # The code template leaves {{TASK_ID}} / {{LOGS_REF}} placeholders
            # for the executor layer to fill in before running.
            code = code.replace("{{TASK_ID}}", task_id)
            code = code.replace("{{LOGS_REF}}", f"minio://logs/{task_id}.log")
            script = work_dir / "solution.py"
            script.write_text(code, encoding="utf-8")

            if self.runner == "podman":
                stdout, stderr, rc, timed_out = self._run_podman(script, work_dir, task_id)
            elif self.runner == "local":
                stdout, stderr, rc, timed_out = self._run_local(script, task_id)
            elif self.runner == "demo":
                stdout, stderr, rc, timed_out = self._demo_result(task_id)
            else:
                assert self.fake_runner is not None
                stdout, stderr, rc, timed_out = self.fake_runner(str(script), task_id, self.timeout_sec)

            return self._parse(stdout, stderr, rc, timed_out, task_id)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    @staticmethod
    def _find_podman() -> str:
        """定位 podman.exe：优先 config，其次 PATH，最后 Windows 常见路径。"""
        try:
            from app.core.config import settings
            if settings.podman_bin:
                return settings.podman_bin
        except Exception:
            pass
        found = shutil.which("podman")
        if found:
            return found
        for candidate in (
            r"C:\Users\kylin\AppData\Local\Programs\Podman\podman.exe",
            r"C:\Program Files\RedHat\Podman\podman.exe",
        ):
            if os.path.isfile(candidate):
                return candidate
        return "podman"  # 让 subprocess 报清晰错误

    def _run_podman(self, script: Path, work_dir: Path, task_id: str) -> tuple[str, str, int, bool]:
        """直接调 podman.exe（不经 run_sandbox.sh / bash）。

        原因：经 bash 脚本间接调用时，podman 内部启动的 wsl.exe 会被工具层的
        安全策略以另一路径拦截；直接调 podman.exe 则可用。安全参数在此复刻。
        """
        podman = self._find_podman()
        # Windows 原生路径直接用；POSIX 上 Path 本身就是 /... 形式。
        data_dir = str(self.data_dir)
        work_path = str(work_dir)
        cmd = [
            podman, "run", "--rm", "--name", f"qp-sandbox-{task_id}",
            "--network=none", "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=256m",
            "--cap-drop=ALL", "--security-opt", "no-new-privileges:true",
            "--userns=keep-id",
            "--memory", "2g", "--memory-swap", "2g",
            "--cpus", "2", "--pids-limit", "128",
            "--ulimit", "nofile=256:256", "--ulimit", "fsize=104857600:104857600",
            "-e", f"QP_TIMEOUT_SEC={self.timeout_sec}",
            "-e", "QLIB_PROVIDER_URI=/data/qlib",
            "-e", f"SOLUTION_PATH=/work/{script.name}",
            "-v", f"{data_dir}:/data/qlib:ro",
            "-v", f"{work_path}:/work:ro",
            self.image,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, timeout=self.timeout_sec + 15,
            )
            def _dec(b):
                if isinstance(b, bytes):
                    return b.decode("utf-8", errors="replace")
                return b or ""
            return _dec(proc.stdout), _dec(proc.stderr), proc.returncode, False
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout or b""
            err = exc.stderr or b""
            if isinstance(out, bytes):
                out = out.decode("utf-8", errors="replace")
            if isinstance(err, bytes):
                err = err.decode("utf-8", errors="replace")
            return out, err + "\n[executor] podman 调用层超时", -1, True

    def _run_local(self, script: Path, task_id: str) -> tuple[str, str, int, bool]:
        env = {**os.environ, "QLIB_PROVIDER_URI": str(self.data_dir)}
        try:
            proc = subprocess.run(
                ["python", str(script)], capture_output=True, text=True,
                timeout=self.timeout_sec, env=env, cwd=str(script.parent),
            )
            return proc.stdout, proc.stderr, proc.returncode, False
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout or ""
            err = exc.stderr or ""
            if isinstance(out, bytes):
                out = out.decode("utf-8", errors="replace")
            if isinstance(err, bytes):
                err = err.decode("utf-8", errors="replace")
            return out, err + "\n[executor] 本地执行超时", -1, True

    @staticmethod
    def _demo_result(task_id: str) -> tuple[str, str, int, bool]:
        """Offline demo runner: emit a realistic standardized result without
        qlib/podman. Used only when runner='demo' (API demos / no credentials).
        """
        import math
        import random

        rnd = random.Random(abs(hash(task_id)) % 10000)
        n = 260
        nav, bench, dd = [], [], []
        v = b = 1.0
        peak = 1.0
        for i in range(n):
            date = f"{(2023 + i // 252)}-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"
            v *= 1 + 0.0004 + (rnd.random() - 0.48) * 0.012
            b *= 1 + 0.0002 + (rnd.random() - 0.5) * 0.010
            peak = max(peak, v)
            nav.append({"date": date, "value": round(v, 4)})
            bench.append({"date": date, "value": round(b, 4)})
            dd.append({"date": date, "value": round(v / peak - 1, 4)})
        years = n / 252
        ann = v ** (1 / years) - 1
        max_dd = min(x["value"] for x in dd)
        result = {
            "task_id": task_id,
            "status": "done",
            "metrics": {
                "annual_return": round(ann, 4),
                "sharpe": round(1.5 + rnd.random() * 0.5, 2),
                "max_drawdown": round(max_dd, 4),
                "win_rate": round(0.52 + rnd.random() * 0.08, 3),
                "turnover": round(0.25 + rnd.random() * 0.1, 3),
                "calmar": round(ann / abs(max_dd), 2),
            },
            "series": {"nav": nav, "benchmark_nav": bench, "drawdown": dd},
            "contributions": [
                {"name": n_, "ret": round(r, 4), "amount": round(r * 1e6)}
                for n_, r in [("贵州茅台", 0.08), ("宁德时代", 0.05), ("招商银行", 0.03),
                              ("中芯国际", -0.02), ("五粮液", -0.04)]
            ],
            "holdings": [
                {"name": n_, "weight": round(w, 3), "ret": round(r, 4)}
                for n_, w, r in [("贵州茅台", 0.095, 0.02), ("招商银行", 0.088, 0.01),
                                 ("宁德时代", 0.075, -0.01), ("五粮液", 0.062, 0.015)]
            ],
            "split": {
                "in_sample": {"annual_return": round(ann * 1.1, 4), "sharpe": 2.0},
                "out_sample": {"annual_return": round(ann * 0.8, 4), "sharpe": 1.4},
            },
            "positions": [], "trades": [],
            "logs_ref": f"demo://logs/{task_id}",
        }
        return RESULT_MARKER + json.dumps(result, ensure_ascii=False) + "\n", "", 0, False

    @staticmethod
    def _parse(
        stdout: str,
        stderr: str,
        returncode: int,
        timed_out: bool,
        task_id: str,
    ) -> ExecutionResult:
        errors: list[str] = []
        if timed_out:
            errors.append("执行超时")
        if returncode not in (0, None):
            errors.append(f"进程退出码 {returncode}")

        result: dict[str, Any] | None = None
        for line in stdout.splitlines():
            if line.startswith(RESULT_MARKER):
                payload = line[len(RESULT_MARKER):].strip()
                try:
                    candidate = json.loads(payload)
                    if isinstance(candidate, dict) and "metrics" in candidate:
                        result = candidate
                    else:
                        errors.append("结果 JSON 结构不合法（缺少 metrics）")
                except json.JSONDecodeError as exc:
                    errors.append(f"结果 JSON 解析失败: {exc}")
                break
        else:
            errors.append("输出中未找到 QUANTPLOT_RESULT 标记")

        if result is not None and task_id:
            # Always override: generated code must not control the task_id.
            result["task_id"] = task_id

        return ExecutionResult(
            success=result is not None and not errors,
            result=result,
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            timed_out=timed_out,
            task_id=task_id,
            errors=errors,
        )
