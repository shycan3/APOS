from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any


class SpecError(ValueError):
    """Raised when a task or runtime spec is invalid."""


def _string_list(data: dict[str, Any], key: str) -> list[str]:
    value = data.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SpecError(f"{key} must be a list of strings")
    return value


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    goal: str
    title: str = ""
    branch: str | None = None
    allowed_files: list[str] = field(default_factory=list)
    read_only_files: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    expected_behavior: list[str] = field(default_factory=list)
    test_commands: list[str] = field(default_factory=list)
    context_requirements: list[str] = field(default_factory=list)
    max_attempts: int | None = None

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "TaskSpec":
        task_id = data.get("task_id")
        goal = data.get("goal")
        if not isinstance(task_id, str) or not task_id.strip():
            raise SpecError("task_id is required")
        if not isinstance(goal, str) or not goal.strip():
            raise SpecError("goal is required")

        title = data.get("title", "")
        if title is None:
            title = ""
        if not isinstance(title, str):
            raise SpecError("title must be a string")

        branch = data.get("branch")
        if branch is not None and not isinstance(branch, str):
            raise SpecError("branch must be a string")

        max_attempts = data.get("max_attempts")
        if max_attempts is not None:
            if not isinstance(max_attempts, int) or max_attempts < 1:
                raise SpecError("max_attempts must be a positive integer")

        spec = cls(
            task_id=task_id.strip(),
            goal=goal.strip(),
            title=title.strip(),
            branch=branch.strip() if isinstance(branch, str) and branch.strip() else None,
            allowed_files=_string_list(data, "allowed_files"),
            read_only_files=_string_list(data, "read_only_files"),
            constraints=_string_list(data, "constraints"),
            expected_behavior=_string_list(data, "expected_behavior"),
            test_commands=_string_list(data, "test_commands"),
            context_requirements=_string_list(data, "context_requirements"),
            max_attempts=max_attempts,
        )
        spec.validate()
        return spec

    @classmethod
    def load(cls, path: Path) -> "TaskSpec":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SpecError(f"invalid JSON in {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise SpecError("TaskSpec must be a JSON object")
        return cls.from_mapping(data)

    def validate(self) -> None:
        if not self.allowed_files:
            raise SpecError("allowed_files must contain at least one path")
        if not self.test_commands:
            raise SpecError("test_commands must contain at least one command")

    def display_title(self) -> str:
        return self.title or self.goal

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "goal": self.goal,
            "branch": self.branch,
            "allowed_files": list(self.allowed_files),
            "read_only_files": list(self.read_only_files),
            "constraints": list(self.constraints),
            "expected_behavior": list(self.expected_behavior),
            "test_commands": list(self.test_commands),
            "context_requirements": list(self.context_requirements),
            "max_attempts": self.max_attempts,
        }


@dataclass(frozen=True)
class PermissionSpec:
    read: list[str]
    write: list[str]
    execute: list[str]

    @classmethod
    def from_task(cls, spec: TaskSpec) -> "PermissionSpec":
        read = sorted(set(spec.allowed_files + spec.read_only_files))
        return cls(read=read, write=list(spec.allowed_files), execute=list(spec.test_commands))


@dataclass(frozen=True)
class ContextRequest:
    type: str
    path: str
    reason: str
    permission: str = "read"


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    stage: str = "COMMAND"
    error_type: str | None = None
    summary: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "PASS" and self.exit_code == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "stage": self.stage,
            "error_type": self.error_type,
            "command": self.command,
            "exit_code": self.exit_code,
            "summary": self.summary,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


@dataclass(frozen=True)
class ReviewResult:
    status: str
    issues: list[str] = field(default_factory=list)
    documentation_updates: list[str] = field(default_factory=list)
    required_changes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AttemptResult:
    attempt: int
    status: str
    message: str
    test_results: list[ExecutionResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "status": self.status,
            "message": self.message,
            "test_results": [result.to_dict() for result in self.test_results],
        }


@dataclass(frozen=True)
class RunSummary:
    status: str
    task_id: str
    branch: str
    attempts: list[AttemptResult]
    committed: bool = False
    commit_hash: str | None = None
    run_log: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "task_id": self.task_id,
            "branch": self.branch,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "committed": self.committed,
            "commit_hash": self.commit_hash,
            "run_log": self.run_log,
        }
