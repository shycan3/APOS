from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from . import __version__
from .benchmark import BenchmarkError, validate_benchmark_suite
from .config import ensure_project_memory, load_config, save_config
from .git import GitClient, GitError
from .kernel import Kernel, KernelError, RunOptions
from .models import SpecError, TaskSpec
from .report import generate_quality_report
from .runlog import list_run_logs, load_run_log


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 0
    try:
        return int(args.handler(args))
    except (BenchmarkError, GitError, KernelError, SpecError) as exc:
        print(f"APOS error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apos", description="APOS 0.1 local AI development runtime")
    parser.add_argument("--version", action="version", version=f"apos {__version__}")

    subcommands = parser.add_subparsers(dest="command")

    init_parser = subcommands.add_parser("init", help="initialize APOS memory in the current Git project")
    init_parser.set_defaults(handler=cmd_init)

    connect_parser = subcommands.add_parser("connect", help="configure a Local Coder command")
    connect_parser.add_argument("--coder-command", required=True, help="command that reads prompt stdin and prints a patch")
    connect_parser.set_defaults(handler=cmd_connect)

    connect_ollama_parser = subcommands.add_parser("connect-ollama", help="configure Ollama as the Local Coder")
    connect_ollama_parser.add_argument("--model", required=True, help="Ollama model name, for example qwen2.5-coder:7b")
    connect_ollama_parser.add_argument("--ollama-binary", default="ollama", help="Ollama executable path")
    connect_ollama_parser.set_defaults(handler=cmd_connect_ollama)

    status_parser = subcommands.add_parser("status", help="show APOS and Git project status")
    status_parser.set_defaults(handler=cmd_status)

    validate_parser = subcommands.add_parser("validate", help="validate a TaskSpec JSON file")
    validate_parser.add_argument("taskspec", type=Path)
    validate_parser.set_defaults(handler=cmd_validate)

    template_parser = subcommands.add_parser("task-template", help="print a minimal TaskSpec template")
    template_parser.set_defaults(handler=cmd_task_template)

    run_parser = subcommands.add_parser("run", help="run an APOS 0.1 task loop")
    run_parser.add_argument("taskspec", type=Path)
    run_parser.add_argument("--coder-command", help="override configured Local Coder command")
    run_parser.add_argument("--max-attempts", type=int, help="override retry budget")
    run_parser.add_argument("--timeout", type=int, help="command timeout in seconds")
    run_parser.add_argument("--no-commit", action="store_true", help="leave successful changes uncommitted")
    run_parser.add_argument("--allow-dirty", action="store_true", help="allow starting from a dirty worktree")
    run_parser.add_argument("--json", action="store_true", help="print machine-readable run summary")
    run_parser.set_defaults(handler=cmd_run)

    runs_parser = subcommands.add_parser("runs", help="inspect APOS run logs")
    runs_subcommands = runs_parser.add_subparsers(dest="runs_command")

    runs_list_parser = runs_subcommands.add_parser("list", help="list recent APOS runs")
    runs_list_parser.add_argument("--limit", type=int, default=20, help="maximum number of runs to show")
    runs_list_parser.add_argument("--json", action="store_true", help="print machine-readable run list")
    runs_list_parser.set_defaults(handler=cmd_runs_list)

    runs_show_parser = runs_subcommands.add_parser("show", help="show one APOS run log")
    runs_show_parser.add_argument("run_log", help="run log path, for example .apos/runs/task-001/<run-id>")
    runs_show_parser.add_argument("--json", action="store_true", help="print machine-readable run details")
    runs_show_parser.set_defaults(handler=cmd_runs_show)

    report_parser = subcommands.add_parser("report", help="generate a compact quality report for one APOS run")
    report_parser.add_argument("run_log", help="run log path, for example .apos/runs/task-001/<run-id>")
    report_parser.add_argument("--json", action="store_true", help="print machine-readable quality report")
    report_parser.set_defaults(handler=cmd_report)

    benchmark_parser = subcommands.add_parser("benchmark", help="inspect benchmark task suites")
    benchmark_subcommands = benchmark_parser.add_subparsers(dest="benchmark_command")

    benchmark_validate_parser = benchmark_subcommands.add_parser("validate", help="validate a benchmark suite")
    benchmark_validate_parser.add_argument("suite", type=Path)
    benchmark_validate_parser.set_defaults(handler=cmd_benchmark_validate)

    benchmark_show_parser = benchmark_subcommands.add_parser("show", help="show benchmark suite metadata")
    benchmark_show_parser.add_argument("suite", type=Path)
    benchmark_show_parser.add_argument("--json", action="store_true", help="print machine-readable suite metadata")
    benchmark_show_parser.set_defaults(handler=cmd_benchmark_show)

    return parser


def cmd_init(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    created = ensure_project_memory(root)
    if created:
        print("APOS initialized:")
        for path in created:
            print(f"  + {path.relative_to(root)}")
    else:
        print("APOS is already initialized.")
    return 0


def cmd_connect(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    ensure_project_memory(root)
    config = load_config(root)
    config.setdefault("local_coder", {})["command"] = args.coder_command
    save_config(root, config)
    print("Local Coder command configured.")
    return 0


def cmd_connect_ollama(args: argparse.Namespace) -> int:
    command = subprocess.list2cmdline(
        [
            sys.executable,
            "-m",
            "apos.ollama",
            "--model",
            args.model,
            "--ollama-binary",
            args.ollama_binary,
        ]
    )
    root = GitClient(Path.cwd()).ensure_repo()
    ensure_project_memory(root)
    config = load_config(root)
    config.setdefault("local_coder", {})["command"] = command
    save_config(root, config)
    print(f"Ollama Local Coder configured: {args.model}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    top = GitClient(Path.cwd()).ensure_repo()
    root = top
    git = GitClient(root)
    config = load_config(root)
    print(f"APOS {__version__}")
    print(f"Project: {top}")
    print(f"Branch: {git.current_branch()}")
    command = config.get("local_coder", {}).get("command") or "<not configured>"
    print(f"Local Coder: {command}")
    status = git.status_porcelain().strip()
    if status:
        print("Git status:")
        print(status)
    else:
        print("Git status: clean")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    spec = TaskSpec.load(args.taskspec)
    print(f"TaskSpec valid: {spec.task_id} ({spec.display_title()})")
    return 0


def cmd_task_template(args: argparse.Namespace) -> int:
    template = {
        "task_id": "TASK-001",
        "title": "Short task title",
        "goal": "Describe the desired project change.",
        "allowed_files": ["path/to/file.py", "tests/test_file.py"],
        "read_only_files": [],
        "constraints": ["Keep public APIs stable unless explicitly required."],
        "expected_behavior": ["Describe observable success criteria."],
        "test_commands": ["python -m unittest discover -s tests"],
        "context_requirements": [],
        "max_attempts": 3,
    }
    print(json.dumps(template, indent=2))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    root = Path.cwd()
    spec = TaskSpec.load(args.taskspec)
    summary = Kernel(root).run_task(
        spec,
        RunOptions(
            coder_command=args.coder_command,
            max_attempts=args.max_attempts,
            no_commit=args.no_commit,
            allow_dirty=args.allow_dirty,
            command_timeout_seconds=args.timeout,
        ),
    )
    if args.json:
        print(json.dumps(summary.to_dict(), indent=2))
    else:
        _print_summary(summary)
    return 0 if summary.status == "PASS" else 2


def cmd_runs_list(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    entries = list_run_logs(root, limit=args.limit)
    if args.json:
        print(json.dumps([entry.to_dict() for entry in entries], indent=2))
        return 0

    if not entries:
        print("No APOS run logs found.")
        return 0

    for entry in entries:
        commit = entry.commit_hash if entry.committed and entry.commit_hash else "-"
        print(f"{entry.relative_path}  {entry.status}  {entry.task_id}  attempts={entry.attempts}  commit={commit}")
    return 0


def cmd_runs_show(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    try:
        detail = load_run_log(root, args.run_log)
    except FileNotFoundError as exc:
        print(f"APOS error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(detail, indent=2, ensure_ascii=False))
        return 0

    _print_run_log(detail)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    try:
        report = generate_quality_report(root, args.run_log)
    except FileNotFoundError as exc:
        print(f"APOS error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    _print_quality_report(report)
    return 0


def cmd_benchmark_validate(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    suite = validate_benchmark_suite(root, args.suite)
    print(f"Benchmark suite valid: {suite.suite_id} ({len(suite.tasks)} task(s))")
    return 0


def cmd_benchmark_show(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    suite = validate_benchmark_suite(root, args.suite)
    if args.json:
        print(json.dumps(suite.to_dict(), indent=2, ensure_ascii=False))
        return 0

    _print_benchmark_suite(suite.to_dict())
    return 0


def _print_summary(summary) -> None:
    print(f"Task: {summary.task_id}")
    print(f"Status: {summary.status}")
    print(f"Branch: {summary.branch}")
    for attempt in summary.attempts:
        print(f"Attempt {attempt.attempt}: {attempt.status}")
        print(f"  {attempt.message}")
        for result in attempt.test_results:
            print(f"  test: {result.command} -> {result.status} ({result.exit_code})")
    if summary.committed:
        print(f"Commit: {summary.commit_hash}")
    elif summary.status == "PASS":
        print("Commit: skipped")
    if summary.run_log:
        print(f"Run log: {summary.run_log}")


def _print_run_log(detail: dict[str, object]) -> None:
    summary = detail.get("summary") if isinstance(detail.get("summary"), dict) else {}
    run = detail.get("run") if isinstance(detail.get("run"), dict) else {}
    attempts = detail.get("attempts") if isinstance(detail.get("attempts"), list) else []

    print(f"Run log: {detail.get('path')}")
    print(f"Task: {summary.get('task_id') or run.get('task_id')}")
    print(f"Title: {run.get('title') or '-'}")
    print(f"Status: {summary.get('status')}")
    print(f"Branch: {summary.get('branch') or run.get('branch')}")
    print(f"Started: {run.get('started_at') or '-'}")
    commit_hash = summary.get("commit_hash")
    if commit_hash:
        print(f"Commit: {commit_hash}")

    for item in attempts:
        if not isinstance(item, dict):
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        response = item.get("response") if isinstance(item.get("response"), dict) else {}
        tests = item.get("tests") if isinstance(item.get("tests"), list) else []
        rollback = item.get("rollback") if isinstance(item.get("rollback"), dict) else None
        print(f"Attempt {result.get('attempt') or item.get('attempt')}: {result.get('status') or 'UNKNOWN'}")
        message = result.get("message")
        if message:
            print(f"  {message}")
        response_type = response.get("type")
        if response_type:
            print(f"  response: {response_type}")
        for test in tests:
            if not isinstance(test, dict):
                continue
            print(f"  test: {test.get('command')} -> {test.get('status')} ({test.get('exit_code')})")
        if rollback:
            print(f"  rollback: {rollback.get('status')} ({rollback.get('message')})")
        if item.get("patch_file"):
            print(f"  patch: {item.get('patch_file')}")


def _print_quality_report(report: dict[str, object]) -> None:
    quality = report.get("quality") if isinstance(report.get("quality"), dict) else {}
    tests = report.get("tests") if isinstance(report.get("tests"), dict) else {}
    responses = report.get("responses") if isinstance(report.get("responses"), dict) else {}
    rollbacks = report.get("rollbacks") if isinstance(report.get("rollbacks"), dict) else {}
    notes = quality.get("notes") if isinstance(quality.get("notes"), list) else []

    print(f"Quality report: {report.get('run_log')}")
    print(f"Task: {report.get('task_id')}")
    print(f"Status: {report.get('status')}")
    print(f"Verdict: {quality.get('verdict')}")
    print(f"Score: {quality.get('score')}")
    print(f"Attempts: {report.get('attempts')}")
    print(f"Tests: {tests.get('passed')}/{tests.get('total')} passed, {tests.get('failed')} failed")
    print(f"Responses: patch={responses.get('patch')}, permission_requests={responses.get('permission_requests')}")
    print(f"Rollbacks: passed={rollbacks.get('passed')}, failed={rollbacks.get('failed')}")
    if report.get("commit_hash"):
        print(f"Commit: {report.get('commit_hash')}")
    for note in notes:
        print(f"- {note}")


def _print_benchmark_suite(suite: dict[str, object]) -> None:
    tasks = suite.get("tasks") if isinstance(suite.get("tasks"), list) else []
    metrics = suite.get("metrics") if isinstance(suite.get("metrics"), list) else []
    print(f"Benchmark suite: {suite.get('suite_id')}")
    print(f"Title: {suite.get('title')}")
    print(f"Version: {suite.get('version')}")
    description = suite.get("description")
    if description:
        print(f"Description: {description}")
    print(f"Tasks: {len(tasks)}")
    for task in tasks:
        if not isinstance(task, dict):
            continue
        tags = task.get("tags") if isinstance(task.get("tags"), list) else []
        tag_text = ",".join(str(tag) for tag in tags) if tags else "-"
        print(
            f"  {task.get('task_id')}  {task.get('path')}  "
            f"category={task.get('category')}  difficulty={task.get('difficulty')}  "
            f"weight={task.get('weight')}  tags={tag_text}"
        )
    if metrics:
        print(f"Metrics: {', '.join(str(metric) for metric in metrics)}")


if __name__ == "__main__":
    raise SystemExit(main())
