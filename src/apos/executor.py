from __future__ import annotations

import subprocess
from pathlib import Path

from .models import ExecutionResult


def run_command(command: str, cwd: Path, timeout_seconds: int) -> ExecutionResult:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return ExecutionResult(
            status="FAILED",
            stage="TEST",
            error_type="TIMEOUT",
            command=command,
            exit_code=124,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            summary=f"command timed out after {timeout_seconds}s",
        )

    status = "PASS" if completed.returncode == 0 else "FAILED"
    summary = "command passed" if status == "PASS" else f"command exited with {completed.returncode}"
    return ExecutionResult(
        status=status,
        stage="TEST",
        error_type=None if status == "PASS" else "COMMAND_FAILED",
        command=command,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        summary=summary,
    )


def run_commands(commands: list[str], cwd: Path, timeout_seconds: int) -> list[ExecutionResult]:
    results: list[ExecutionResult] = []
    for command in commands:
        result = run_command(command, cwd=cwd, timeout_seconds=timeout_seconds)
        results.append(result)
        if not result.passed:
            break
    return results

