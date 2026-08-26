from __future__ import annotations

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
