from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from .config import apos_dir
from .git import GitClient
from .kernel import Kernel, RunOptions
from .models import SpecError, TaskSpec
from .pathing import project_path
from .report import generate_quality_report


class BenchmarkError(ValueError):
    """Raised when a benchmark suite is invalid."""


def _slug(value: str) -> str:
    slug = "".join(character.lower() if character.isalnum() else "-" for character in value).strip("-")
    return "-".join(part for part in slug.split("-") if part) or "benchmark"


@dataclass(frozen=True)
class BenchmarkTaskRef:
    task_id: str
    path: str
    category: str = "general"
    difficulty: str = "unknown"
    weight: int = 1
    expected_minutes: int | None = None
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "BenchmarkTaskRef":
        task_id = _required_string(data, "task_id")
        path = _required_string(data, "path")
        category = _optional_string(data, "category", "general")
        difficulty = _optional_string(data, "difficulty", "unknown")
        weight = data.get("weight", 1)
        if not isinstance(weight, int) or weight < 1:
            raise BenchmarkError("task weight must be a positive integer")
        expected_minutes = data.get("expected_minutes")
        if expected_minutes is not None and (not isinstance(expected_minutes, int) or expected_minutes < 1):
            raise BenchmarkError("expected_minutes must be a positive integer")
        tags = data.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(item, str) for item in tags):
            raise BenchmarkError("tags must be a list of strings")
        return cls(
            task_id=task_id,
            path=path,
            category=category,
            difficulty=difficulty,
            weight=weight,
            expected_minutes=expected_minutes,
            tags=tags,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "path": self.path,
            "category": self.category,
            "difficulty": self.difficulty,
            "weight": self.weight,
            "expected_minutes": self.expected_minutes,
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class BenchmarkSuite:
    suite_id: str
    title: str
    description: str
    version: str = "0.1"
    tasks: list[BenchmarkTaskRef] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "BenchmarkSuite":
        suite_id = _required_string(data, "suite_id")
        title = _required_string(data, "title")
        description = _optional_string(data, "description", "")
        version = _optional_string(data, "version", "0.1")
        tasks_data = data.get("tasks")
        if not isinstance(tasks_data, list) or not tasks_data:
            raise BenchmarkError("tasks must contain at least one benchmark task")
        tasks = []
        seen: set[str] = set()
        for item in tasks_data:
            if not isinstance(item, dict):
                raise BenchmarkError("each benchmark task must be an object")
            task = BenchmarkTaskRef.from_mapping(item)
            if task.task_id in seen:
                raise BenchmarkError(f"duplicate benchmark task_id: {task.task_id}")
            seen.add(task.task_id)
            tasks.append(task)
        metrics = data.get("metrics", ["quality.score", "attempts", "tests.failed"])
        if not isinstance(metrics, list) or not all(isinstance(item, str) for item in metrics):
            raise BenchmarkError("metrics must be a list of strings")
        return cls(
            suite_id=suite_id,
            title=title,
            description=description,
            version=version,
            tasks=tasks,
            metrics=metrics,
        )

    @classmethod
    def load(cls, path: Path) -> "BenchmarkSuite":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise BenchmarkError(f"invalid JSON in {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise BenchmarkError("benchmark suite must be a JSON object")
        return cls.from_mapping(data)

    def to_dict(self) -> dict[str, object]:
        return {
            "suite_id": self.suite_id,
            "title": self.title,
            "description": self.description,
            "version": self.version,
            "tasks": [task.to_dict() for task in self.tasks],
            "metrics": list(self.metrics),
        }


def validate_benchmark_suite(root: Path, suite_path: Path) -> BenchmarkSuite:
    suite = BenchmarkSuite.load(suite_path)
    for task in suite.tasks:
        task_path = project_path(root, task.path)
        if not task_path.exists():
            raise BenchmarkError(f"benchmark task file not found: {task.path}")
        try:
            spec = TaskSpec.load(task_path)
        except SpecError as exc:
            raise BenchmarkError(f"invalid TaskSpec for {task.task_id}: {exc}") from exc
        if spec.task_id != task.task_id:
            raise BenchmarkError(f"task_id mismatch for {task.path}: suite has {task.task_id}, TaskSpec has {spec.task_id}")
    return suite


@dataclass(frozen=True)
class BenchmarkRunOptions:
    coder_command: str | None = None
    max_attempts: int | None = None
    no_commit: bool = False
    allow_dirty: bool = False
    command_timeout_seconds: int | None = None
    keep_going: bool = False


def run_benchmark_suite(root: Path, suite_path: Path, options: BenchmarkRunOptions) -> dict[str, object]:
    root = GitClient(root).ensure_repo()
    suite = validate_benchmark_suite(root, suite_path)
    started_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result_id = f"{started_at}-{uuid4().hex[:8]}"

    tasks: list[dict[str, object]] = []
    for task_ref in suite.tasks:
        spec = TaskSpec.load(project_path(root, task_ref.path))
        started = perf_counter()
        summary = Kernel(root).run_task(
            spec,
            RunOptions(
                coder_command=options.coder_command,
                max_attempts=options.max_attempts,
                no_commit=options.no_commit,
                allow_dirty=options.allow_dirty,
                command_timeout_seconds=options.command_timeout_seconds,
            ),
        )
        duration_seconds = round(perf_counter() - started, 3)
        report = generate_quality_report(root, summary.run_log) if summary.run_log else None
        tasks.append(
            {
                "task": task_ref.to_dict(),
                "status": summary.status,
                "duration_seconds": duration_seconds,
                "run_log": summary.run_log,
                "summary": summary.to_dict(),
                "report": report,
            }
        )
        if summary.status != "PASS" and not options.keep_going:
            break

    result = {
        "suite": suite.to_dict(),
        "started_at": started_at,
        "result_id": result_id,
        "status": _benchmark_status(tasks, len(suite.tasks)),
        "tasks": tasks,
        "summary": _benchmark_summary(tasks, len(suite.tasks)),
    }
    result_path = _benchmark_result_path(root, suite.suite_id, result_id)
    result["result_path"] = result_path.relative_to(root).as_posix()
    GitClient(root).exclude_path(".apos/benchmarks/")
    _write_json(result_path, result)
    return result


def _benchmark_result_path(root: Path, suite_id: str, result_id: str) -> Path:
    directory = apos_dir(root) / "benchmarks" / _slug(suite_id) / result_id
    directory.mkdir(parents=True, exist_ok=False)
    return directory / "result.json"


def _benchmark_status(tasks: list[dict[str, object]], total_tasks: int) -> str:
    if len(tasks) < total_tasks:
        return "FAILED"
    if all(task.get("status") == "PASS" for task in tasks):
        return "PASS"
    return "FAILED"


def _benchmark_summary(tasks: list[dict[str, object]], total_tasks: int) -> dict[str, object]:
    completed = len(tasks)
    passed = sum(1 for task in tasks if task.get("status") == "PASS")
    failed = completed - passed
    scores: list[int] = []
    for task in tasks:
        report = task.get("report")
        if not isinstance(report, dict):
            continue
        quality = report.get("quality")
        if not isinstance(quality, dict):
            continue
        score = quality.get("score")
        if isinstance(score, int):
            scores.append(score)
    average_score = round(sum(scores) / len(scores), 2) if scores else None
    return {
        "total_tasks": total_tasks,
        "completed_tasks": completed,
        "passed_tasks": passed,
        "failed_tasks": failed,
        "average_quality_score": average_score,
    }


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkError(f"{key} is required")
    return value.strip()


def _optional_string(data: dict[str, Any], key: str, default: str) -> str:
    value = data.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise BenchmarkError(f"{key} must be a string")
    return value.strip()
