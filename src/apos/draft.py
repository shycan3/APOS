from __future__ import annotations

import json
from pathlib import Path
import re

from .models import TaskSpec
from .ollama import run_ollama_generate, run_ollama_prompt


class DraftError(ValueError):
    """Raised when APOS cannot draft or refine a TaskSpec."""


REFINE_PROMPT = """You are APOS TaskSpec Planner.

Refine the provided APOS TaskSpec into a clearer, executable TaskSpec JSON object.

Rules:
- Return only one JSON object.
- Preserve task_id.
- Preserve allowed_files, read_only_files, test_commands, branch, and max_attempts unless they are missing.
- Do not invent file paths.
- Improve title, constraints, expected_behavior, and context_requirements when useful.
- Keep the JSON compatible with APOS TaskSpec.
"""


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


def refine_task_spec_with_ollama(
    spec: TaskSpec,
    model: str,
    ollama_binary: str,
    timeout_seconds: int,
    ollama_host: str = "http://127.0.0.1:11434",
) -> TaskSpec:
    prompt = f"{REFINE_PROMPT}\n\nTASKSPEC JSON:\n{json.dumps(spec.to_dict(), indent=2, ensure_ascii=False)}\n"
    try:
        output = run_ollama_generate(
            model=model,
            prompt=prompt,
            ollama_host=ollama_host,
            timeout_seconds=timeout_seconds,
            json_format=True,
        )
    except RuntimeError:
        output = run_ollama_prompt(
            model=model,
            prompt=prompt,
            ollama_binary=ollama_binary,
            timeout_seconds=timeout_seconds,
        )
    payload = extract_task_spec_json(output)
    refined = TaskSpec.from_mapping(payload)
    _ensure_preserved_fields(spec, refined)
    return refined


def extract_task_spec_json(output: str) -> dict[str, object]:
    stripped = output.strip()
    if not stripped:
        raise DraftError("model returned empty output")
    fenced = _extract_fenced_block(stripped)
    if fenced:
        stripped = fenced
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as first_error:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise DraftError(f"model output did not contain valid JSON: {first_error}") from first_error
        try:
            data = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise DraftError(f"model output did not contain valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DraftError("model output must be a JSON object")
    return data


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


def _ensure_preserved_fields(original: TaskSpec, refined: TaskSpec) -> None:
    preserved = {
        "task_id": (original.task_id, refined.task_id),
        "allowed_files": (original.allowed_files, refined.allowed_files),
        "read_only_files": (original.read_only_files, refined.read_only_files),
        "test_commands": (original.test_commands, refined.test_commands),
        "branch": (original.branch, refined.branch),
        "max_attempts": (original.max_attempts, refined.max_attempts),
    }
    changed = [key for key, values in preserved.items() if values[0] != values[1]]
    if changed:
        raise DraftError(f"refined TaskSpec changed preserved field(s): {', '.join(changed)}")


def _extract_fenced_block(value: str) -> str:
    lines = value.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip().startswith("```"):
            start = index + 1
            break
    if start is None:
        return ""
    for index in range(start, len(lines)):
        if lines[index].strip().startswith("```"):
            return "\n".join(lines[start:index]).strip()
    return ""
