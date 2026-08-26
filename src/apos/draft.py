from __future__ import annotations

import json
from pathlib import Path
import re

from .models import TaskSpec


def draft_task_spec(
    root: Path,
    goal: str,
    allowed_files: list[str],
    test_commands: list[str],
    task_id: str | None = None,
    title: str | None = None,
    read_only_files: list[str] | None = None,
    constraints: list[str] | None = None,
    expected_behavior: list[str] | None = None,
    context_requirements: list[str] | None = None,
    max_attempts: int = 3,
) -> TaskSpec:
    spec = TaskSpec.from_mapping(
        {
            "task_id": task_id or next_task_id(root),
            "title": title or title_from_goal(goal),
            "goal": goal,
            "allowed_files": allowed_files,
            "read_only_files": read_only_files or [],
            "constraints": constraints or ["Keep the change scoped to the requested behavior."],
            "expected_behavior": expected_behavior or [goal],
            "test_commands": test_commands,
            "context_requirements": context_requirements or [],
            "max_attempts": max_attempts,
        }
    )
    return spec


def write_task_spec(path: Path, spec: TaskSpec) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def next_task_id(root: Path) -> str:
    highest = 0
    for path in (root / "tasks").glob("*.json"):
        match = re.search(r"TASK-(\d+)", path.read_text(encoding="utf-8", errors="replace"))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"TASK-{highest + 1:03d}"


def title_from_goal(goal: str) -> str:
    words = re.findall(r"[A-Za-z0-9가-힣]+", goal.strip())
    if not words:
        return "Untitled task"
    return " ".join(words[:8])
