from __future__ import annotations

from pathlib import Path

from .runlog import load_run_log


def generate_quality_report(root: Path, run_log: str) -> dict[str, object]:
    return build_quality_report(load_run_log(root, run_log))


def build_quality_report(detail: dict[str, object]) -> dict[str, object]:
    summary = _dict(detail.get("summary"))
    run = _dict(detail.get("run"))
    task = _dict(detail.get("task"))
    attempts = _list(detail.get("attempts"))

    attempt_statuses: list[str] = []
    patch_responses = 0
    permission_requests = 0
    tests_total = 0
    tests_passed = 0
    tests_failed = 0
    rollbacks_passed = 0
    rollbacks_failed = 0

    for item in attempts:
        attempt = _dict(item)
        result = _dict(attempt.get("result"))
        response = _dict(attempt.get("response"))
        rollback = _dict(attempt.get("rollback"))
        tests = _list(attempt.get("tests"))

        status = str(result.get("status") or "UNKNOWN")
        attempt_statuses.append(status)

        response_type = response.get("type")
        if response_type == "patch":
            patch_responses += 1
        elif response_type == "request_permission":
            permission_requests += 1

        for test in tests:
            test_result = _dict(test)
            tests_total += 1
            if test_result.get("status") == "PASS" and test_result.get("exit_code") == 0:
                tests_passed += 1
            else:
                tests_failed += 1

        rollback_status = rollback.get("status")
        if rollback_status == "PASS":
            rollbacks_passed += 1
        elif rollback_status == "FAILED":
            rollbacks_failed += 1

    status = str(summary.get("status") or "UNKNOWN")
    score = _score(
        status=status,
        attempts=len(attempts),
        tests_failed=tests_failed,
        permission_requests=permission_requests,
        rollbacks_failed=rollbacks_failed,
        committed=bool(summary.get("committed")),
    )

    return {
        "run_log": detail.get("path"),
        "task_id": summary.get("task_id") or run.get("task_id") or task.get("task_id"),
        "title": run.get("title") or task.get("title") or "",
        "status": status,
        "branch": summary.get("branch") or run.get("branch"),
        "commit_hash": summary.get("commit_hash"),
        "committed": bool(summary.get("committed")),
        "attempts": len(attempts),
        "attempt_statuses": attempt_statuses,
        "responses": {
            "patch": patch_responses,
            "permission_requests": permission_requests,
        },
        "tests": {
            "total": tests_total,
            "passed": tests_passed,
            "failed": tests_failed,
        },
        "rollbacks": {
            "passed": rollbacks_passed,
            "failed": rollbacks_failed,
        },
        "quality": {
            "score": score,
            "verdict": _verdict(status, score),
            "notes": _notes(status, len(attempts), tests_total, tests_failed, permission_requests, rollbacks_failed),
        },
    }


def _score(
    status: str,
    attempts: int,
    tests_failed: int,
    permission_requests: int,
    rollbacks_failed: int,
    committed: bool,
) -> int:
    score = 100 if status == "PASS" else 45
    score -= max(0, attempts - 1) * 10
    score -= tests_failed * 15
    score -= permission_requests * 10
    score -= rollbacks_failed * 25
    if status == "PASS" and not committed:
        score -= 5
    return max(0, min(100, score))


def _verdict(status: str, score: int) -> str:
    if status == "PASS" and score >= 90:
        return "ready"
    if status == "PASS":
        return "usable"
    if status == "NEEDS_PERMISSION":
        return "blocked"
    return "failed"


def _notes(
    status: str,
    attempts: int,
    tests_total: int,
    tests_failed: int,
    permission_requests: int,
    rollbacks_failed: int,
) -> list[str]:
    notes: list[str] = []
    if status == "PASS":
        notes.append("Task completed successfully.")
    else:
        notes.append(f"Task ended with status {status}.")
    if attempts > 1:
        notes.append(f"Required {attempts} attempts.")
    if tests_total == 0:
        notes.append("No verification commands were recorded.")
    elif tests_failed:
        notes.append(f"{tests_failed} verification command(s) failed.")
    if permission_requests:
        notes.append(f"{permission_requests} permission request(s) were raised.")
    if rollbacks_failed:
        notes.append(f"{rollbacks_failed} rollback(s) failed.")
    return notes


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []
