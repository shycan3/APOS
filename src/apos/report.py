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
    failure_counts: dict[str, int] = {}
    failure_examples: dict[str, str] = {}

    for item in attempts:
        attempt = _dict(item)
        result = _dict(attempt.get("result"))
        response = _dict(attempt.get("response"))
        rollback = _dict(attempt.get("rollback"))
        tests = _list(attempt.get("tests"))

        status = str(result.get("status") or "UNKNOWN")
        attempt_statuses.append(status)
        message = str(result.get("message") or "")

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

        for code in _attempt_failure_codes(status, response_type, message, tests, rollback_status):
            failure_counts[code] = failure_counts.get(code, 0) + 1
            if message and code not in failure_examples:
                failure_examples[code] = message

    status = str(summary.get("status") or "UNKNOWN")
    failure = _failure_summary(status, failure_counts, failure_examples)
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
        "failure": failure,
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


def _attempt_failure_codes(
    status: str,
    response_type: object,
    message: str,
    tests: list[object],
    rollback_status: object,
) -> list[str]:
    if status == "PASS":
        return []

    codes: list[str] = []
    normalized_message = message.lower()
    failed_tests = sum(1 for test in tests if not _test_passed(_dict(test)))

    if response_type == "request_permission" or status == "NEEDS_PERMISSION":
        codes.append("permission_required")
    if "unauthorized write path" in normalized_message:
        codes.append("unauthorized_write")
    if "did not contain any changed file paths" in normalized_message:
        codes.append("empty_patch")
    if "git apply" in normalized_message and "failed" in normalized_message:
        codes.append("patch_apply_failed")
    if response_type not in ("patch", "request_permission"):
        codes.append("patch_generation_failed")
    if failed_tests:
        codes.append("verification_failed")
    if rollback_status == "FAILED" or "rollback failed" in normalized_message:
        codes.append("rollback_failed")
    if not codes:
        codes.append("unknown_failure")
    return codes


def _test_passed(test: dict[str, object]) -> bool:
    return test.get("status") == "PASS" and test.get("exit_code") == 0


def _failure_summary(status: str, failure_counts: dict[str, int], failure_examples: dict[str, str]) -> dict[str, object]:
    primary = _primary_failure(status, failure_counts)
    return {
        "primary": primary,
        "recovered": status == "PASS" and bool(failure_counts),
        "reasons": [
            {
                "code": code,
                "count": count,
                "example": failure_examples.get(code, ""),
            }
            for code, count in sorted(failure_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


def _primary_failure(status: str, failure_counts: dict[str, int]) -> str:
    if not failure_counts:
        return "none"
    if status == "PASS":
        return "recovered"
    precedence = [
        "rollback_failed",
        "permission_required",
        "unauthorized_write",
        "patch_apply_failed",
        "empty_patch",
        "verification_failed",
        "patch_generation_failed",
        "unknown_failure",
    ]
    for code in precedence:
        if failure_counts.get(code, 0) > 0:
            return code
    return "unknown_failure"


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
