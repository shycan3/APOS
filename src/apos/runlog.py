from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from uuid import uuid4

from .config import apos_dir
from .models import AttemptResult, ContextRequest, ExecutionResult, RunSummary, TaskSpec


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return slug or "task"


@dataclass(frozen=True)
class RunLogEntry:
    path: Path
    relative_path: str
    task_id: str
    title: str
    status: str
    branch: str
    started_at: str
    attempts: int
    committed: bool
    commit_hash: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.relative_path,
            "task_id": self.task_id,
            "title": self.title,
            "status": self.status,
            "branch": self.branch,
            "started_at": self.started_at,
            "attempts": self.attempts,
            "committed": self.committed,
            "commit_hash": self.commit_hash,
        }


class RunRecorder:
    def __init__(self, root: Path, spec: TaskSpec, branch: str) -> None:
        self.root = root
        started_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_id = f"{started_at}-{uuid4().hex[:8]}"
        self.path = apos_dir(root) / "runs" / _slug(spec.task_id) / run_id
        self.path.mkdir(parents=True, exist_ok=False)
        self.write_json(
            "run.json",
            {
                "task_id": spec.task_id,
                "title": spec.display_title(),
                "branch": branch,
                "started_at": started_at,
            },
        )
        self.write_json("task.json", spec.to_dict())

    def relative_path(self) -> str:
        return self.path.relative_to(self.root).as_posix()

    def record_prompt(self, attempt: int, prompt: str) -> None:
        self.write_text(f"attempt-{attempt:02d}/prompt.json", prompt)

    def record_response(self, attempt: int, response_type: str, patch: str, message: str, request: ContextRequest | None) -> None:
        self.write_json(
            f"attempt-{attempt:02d}/response.json",
            {
                "type": response_type,
                "message": message,
                "request": _request_to_dict(request),
                "patch_file": "response.patch" if patch else None,
            },
        )
        if patch:
            self.write_text(f"attempt-{attempt:02d}/response.patch", patch)

    def record_tests(self, attempt: int, results: list[ExecutionResult]) -> None:
        self.write_json(f"attempt-{attempt:02d}/tests.json", [result.to_dict() for result in results])

    def record_rollback(self, attempt: int, status: str, message: str) -> None:
        self.write_json(
            f"attempt-{attempt:02d}/rollback.json",
            {
                "status": status,
                "message": message,
            },
        )

    def record_attempt(self, attempt: AttemptResult) -> None:
        self.write_json(f"attempt-{attempt.attempt:02d}/attempt.json", attempt.to_dict())

    def record_summary(self, summary: RunSummary) -> None:
        self.write_json("summary.json", summary.to_dict())

    def write_json(self, relative: str, data: object) -> None:
        self.write_text(relative, json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    def write_text(self, relative: str, text: str) -> None:
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def _request_to_dict(request: ContextRequest | None) -> dict[str, str] | None:
    if request is None:
        return None
    return {
        "type": request.type,
        "path": request.path,
        "permission": request.permission,
        "reason": request.reason,
    }


def list_run_logs(root: Path, limit: int = 20) -> list[RunLogEntry]:
    runs_root = apos_dir(root) / "runs"
    if not runs_root.exists():
        return []

    entries: list[RunLogEntry] = []
    for summary_path in runs_root.glob("*/*/summary.json"):
        entry = _load_run_log_entry(root, summary_path.parent)
        if entry is not None:
            entries.append(entry)

    entries.sort(key=lambda entry: (entry.started_at, entry.relative_path), reverse=True)
    return entries[:limit]


def load_run_log(root: Path, run_path: str) -> dict[str, object]:
    path = resolve_run_log_path(root, run_path)
    summary = _read_json(path / "summary.json")
    run = _read_json(path / "run.json")
    task = _read_json(path / "task.json")
    attempts = []
    for attempt_path in sorted(path.glob("attempt-*")):
        if not attempt_path.is_dir():
            continue
        attempts.append(
            {
                "attempt": attempt_path.name,
                "result": _read_json_if_exists(attempt_path / "attempt.json"),
                "response": _read_json_if_exists(attempt_path / "response.json"),
                "tests": _read_json_if_exists(attempt_path / "tests.json"),
                "rollback": _read_json_if_exists(attempt_path / "rollback.json"),
                "prompt_file": _relative_if_exists(root, attempt_path / "prompt.json"),
                "patch_file": _relative_if_exists(root, attempt_path / "response.patch"),
            }
        )
    return {
        "path": path.relative_to(root).as_posix(),
        "run": run,
        "task": task,
        "summary": summary,
        "attempts": attempts,
    }


def resolve_run_log_path(root: Path, run_path: str) -> Path:
    candidate = Path(run_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    runs_root = (apos_dir(root) / "runs").resolve()
    try:
        candidate.relative_to(runs_root)
    except ValueError as exc:
        raise FileNotFoundError(f"run log is outside .apos/runs: {run_path}") from exc
    if not candidate.exists() or not candidate.is_dir():
        raise FileNotFoundError(f"run log not found: {run_path}")
    return candidate


def _load_run_log_entry(root: Path, path: Path) -> RunLogEntry | None:
    try:
        summary = _read_json(path / "summary.json")
        run = _read_json(path / "run.json")
    except (OSError, json.JSONDecodeError, TypeError):
        return None

    return RunLogEntry(
        path=path,
        relative_path=path.relative_to(root).as_posix(),
        task_id=str(summary.get("task_id") or run.get("task_id") or ""),
        title=str(run.get("title") or ""),
        status=str(summary.get("status") or "UNKNOWN"),
        branch=str(summary.get("branch") or run.get("branch") or ""),
        started_at=str(run.get("started_at") or ""),
        attempts=len(summary.get("attempts") or []),
        committed=bool(summary.get("committed")),
        commit_hash=summary.get("commit_hash") if isinstance(summary.get("commit_hash"), str) else None,
    )


def _read_json(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return data


def _read_json_if_exists(path: Path) -> object | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _relative_if_exists(root: Path, path: Path) -> str | None:
    if not path.exists():
        return None
    return path.relative_to(root).as_posix()
