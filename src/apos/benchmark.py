from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from . import __version__
from .config import apos_dir, configured_coder_command, configured_ollama
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


@dataclass(frozen=True)
class BenchmarkResultEntry:
    path: Path
    relative_path: str
    suite_id: str
    result_id: str
    status: str
    started_at: str
    total_tasks: int
    passed_tasks: int
    average_quality_score: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.relative_path,
            "suite_id": self.suite_id,
            "result_id": self.result_id,
            "status": self.status,
            "started_at": self.started_at,
            "total_tasks": self.total_tasks,
            "passed_tasks": self.passed_tasks,
            "average_quality_score": self.average_quality_score,
        }


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
        "runner_profile": _runner_profile(root, options),
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


def list_benchmark_results(root: Path, limit: int = 20) -> list[BenchmarkResultEntry]:
    benchmarks_root = apos_dir(root) / "benchmarks"
    if not benchmarks_root.exists():
        return []

    entries: list[BenchmarkResultEntry] = []
    for result_path in benchmarks_root.glob("*/*/result.json"):
        entry = _load_benchmark_result_entry(root, result_path)
        if entry is not None:
            entries.append(entry)

    entries.sort(key=lambda entry: (entry.started_at, entry.relative_path), reverse=True)
    return entries[:limit]


def load_benchmark_result(root: Path, result_path: str) -> dict[str, object]:
    path = resolve_benchmark_result_path(root, result_path)
    result = _read_json(path)
    result.setdefault("result_path", path.relative_to(root).as_posix())
    return result


def compare_benchmark_results(root: Path, result_paths: list[str]) -> dict[str, object]:
    if len(result_paths) < 2:
        raise BenchmarkError("at least two benchmark results are required for comparison")

    entries = [_comparison_entry(load_benchmark_result(root, result_path), index) for index, result_path in enumerate(result_paths)]
    ranked = sorted(
        entries,
        key=lambda entry: (
            entry["average_quality_score"] if isinstance(entry["average_quality_score"], int | float) else -1,
            entry["passed_tasks"],
            -entry["total_duration_seconds"],
            -entry["input_order"],
        ),
        reverse=True,
    )
    for rank, entry in enumerate(ranked, start=1):
        entry["rank"] = rank
    best = ranked[0] if ranked else None
    return {
        "kind": "benchmark_comparison",
        "results": ranked,
        "summary": {
            "result_count": len(entries),
            "best_result_id": best.get("result_id") if best else None,
            "best_suite_id": best.get("suite_id") if best else None,
            "best_score": best.get("average_quality_score") if best else None,
        },
    }


def resolve_benchmark_result_path(root: Path, result_path: str) -> Path:
    candidate = Path(result_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    if candidate.is_dir():
        candidate = candidate / "result.json"
    benchmarks_root = (apos_dir(root) / "benchmarks").resolve()
    try:
        candidate.relative_to(benchmarks_root)
    except ValueError as exc:
        raise FileNotFoundError(f"benchmark result is outside .apos/benchmarks: {result_path}") from exc
    if not candidate.exists() or not candidate.is_file():
        raise FileNotFoundError(f"benchmark result not found: {result_path}")
    return candidate


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
    primary_failures: dict[str, int] = {}
    failure_reasons: dict[str, int] = {}
    for task in tasks:
        report = task.get("report")
        if not isinstance(report, dict):
            continue
        quality = report.get("quality")
        if not isinstance(quality, dict):
            quality = {}
        score = quality.get("score")
        if isinstance(score, int):
            scores.append(score)
        failure = report.get("failure")
        if not isinstance(failure, dict):
            continue
        primary = failure.get("primary")
        if isinstance(primary, str) and primary != "none":
            primary_failures[primary] = primary_failures.get(primary, 0) + 1
        reasons = failure.get("reasons")
        if not isinstance(reasons, list):
            continue
        for reason in reasons:
            if not isinstance(reason, dict):
                continue
            code = reason.get("code")
            count = reason.get("count")
            if isinstance(code, str) and isinstance(count, int):
                failure_reasons[code] = failure_reasons.get(code, 0) + count
    average_score = round(sum(scores) / len(scores), 2) if scores else None
    return {
        "total_tasks": total_tasks,
        "completed_tasks": completed,
        "passed_tasks": passed,
        "failed_tasks": failed,
        "average_quality_score": average_score,
        "primary_failures": dict(sorted(primary_failures.items())),
        "failure_reasons": dict(sorted(failure_reasons.items())),
    }


def _comparison_entry(result: dict[str, object], input_order: int) -> dict[str, object]:
    suite = result.get("suite") if isinstance(result.get("suite"), dict) else {}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    runner_profile = result.get("runner_profile") if isinstance(result.get("runner_profile"), dict) else {}
    ollama = runner_profile.get("ollama") if isinstance(runner_profile.get("ollama"), dict) else {}
    tasks = result.get("tasks") if isinstance(result.get("tasks"), list) else []
    total_duration = round(
        sum(item.get("duration_seconds") for item in tasks if isinstance(item, dict) and isinstance(item.get("duration_seconds"), int | float)),
        3,
    )
    total_tasks = int(summary.get("total_tasks") or 0)
    passed_tasks = int(summary.get("passed_tasks") or 0)
    average_score = summary.get("average_quality_score")
    return {
        "input_order": input_order,
        "rank": None,
        "result_path": str(result.get("result_path") or ""),
        "suite_id": str(suite.get("suite_id") or ""),
        "result_id": str(result.get("result_id") or ""),
        "status": str(result.get("status") or "UNKNOWN"),
        "started_at": str(result.get("started_at") or ""),
        "total_tasks": total_tasks,
        "passed_tasks": passed_tasks,
        "pass_rate": round(passed_tasks / total_tasks, 4) if total_tasks else 0.0,
        "average_quality_score": float(average_score) if isinstance(average_score, int | float) else None,
        "total_duration_seconds": total_duration,
        "apos_version": str(runner_profile.get("apos_version") or ""),
        "coder_command": str(runner_profile.get("coder_command") or ""),
        "ollama_model": str(ollama.get("model") or ""),
    }


def _runner_profile(root: Path, options: BenchmarkRunOptions) -> dict[str, object]:
    model, binary, host = configured_ollama(root)
    return {
        "apos_version": __version__,
        "coder_command": options.coder_command or configured_coder_command(root),
        "ollama": {
            "model": model,
            "binary": binary,
            "host": host,
        },
        "options": {
            "max_attempts": options.max_attempts,
            "no_commit": options.no_commit,
            "allow_dirty": options.allow_dirty,
            "command_timeout_seconds": options.command_timeout_seconds,
            "keep_going": options.keep_going,
        },
    }


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BenchmarkError(f"{path} must contain a JSON object")
    return data


def _load_benchmark_result_entry(root: Path, path: Path) -> BenchmarkResultEntry | None:
    try:
        result = _read_json(path)
    except (OSError, json.JSONDecodeError, TypeError, BenchmarkError):
        return None
    suite = result.get("suite") if isinstance(result.get("suite"), dict) else {}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    average = summary.get("average_quality_score")
    return BenchmarkResultEntry(
        path=path,
        relative_path=path.relative_to(root).as_posix(),
        suite_id=str(suite.get("suite_id") or ""),
        result_id=str(result.get("result_id") or path.parent.name),
        status=str(result.get("status") or "UNKNOWN"),
        started_at=str(result.get("started_at") or ""),
        total_tasks=int(summary.get("total_tasks") or 0),
        passed_tasks=int(summary.get("passed_tasks") or 0),
        average_quality_score=float(average) if isinstance(average, int | float) else None,
    )


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
