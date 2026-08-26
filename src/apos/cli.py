from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from . import __version__
from .benchmark import (
    BenchmarkError,
    BenchmarkRunOptions,
    compare_benchmark_results,
    list_benchmark_results,
    load_benchmark_result,
    run_benchmark_suite,
    validate_benchmark_suite,
)
from .config import configured_coder_command, configured_ollama, ensure_project_memory, load_config, save_config
from .draft import DraftError, draft_task_spec, refine_task_spec_with_ollama, write_task_spec
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
    except (BenchmarkError, DraftError, GitError, KernelError, SpecError) as exc:
        print(f"APOS error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apos", description="APOS 0.1 local AI development runtime")
    parser.add_argument("--version", action="version", version=f"apos {__version__}")

    subcommands = parser.add_subparsers(dest="command")

    init_parser = subcommands.add_parser("init", help="initialize APOS memory in the current Git project")
    init_parser.set_defaults(handler=cmd_init)

    bootstrap_parser = subcommands.add_parser("bootstrap", help="initialize APOS and optionally configure Ollama")
    bootstrap_parser.add_argument("--ollama-model", help="configure this Ollama model as the Local Coder")
    bootstrap_parser.add_argument("--ollama-binary", default="ollama", help="Ollama executable path")
    bootstrap_parser.add_argument("--ollama-host", default="http://127.0.0.1:11434", help="Ollama HTTP API host")
    bootstrap_parser.set_defaults(handler=cmd_bootstrap)

    connect_parser = subcommands.add_parser("connect", help="configure a Local Coder command")
    connect_parser.add_argument("--coder-command", required=True, help="command that reads prompt stdin and prints a patch")
    connect_parser.set_defaults(handler=cmd_connect)

    connect_ollama_parser = subcommands.add_parser("connect-ollama", help="configure Ollama as the Local Coder")
    connect_ollama_parser.add_argument("--model", required=True, help="Ollama model name, for example qwen2.5-coder:7b")
    connect_ollama_parser.add_argument("--ollama-binary", default="ollama", help="Ollama executable path")
    connect_ollama_parser.add_argument("--ollama-host", default="http://127.0.0.1:11434", help="Ollama HTTP API host")
    connect_ollama_parser.set_defaults(handler=cmd_connect_ollama)

    status_parser = subcommands.add_parser("status", help="show APOS and Git project status")
    status_parser.set_defaults(handler=cmd_status)

    validate_parser = subcommands.add_parser("validate", help="validate a TaskSpec JSON file")
    validate_parser.add_argument("taskspec", type=Path)
    validate_parser.set_defaults(handler=cmd_validate)

    template_parser = subcommands.add_parser("task-template", help="print a minimal TaskSpec template")
    template_parser.set_defaults(handler=cmd_task_template)

    draft_parser = subcommands.add_parser("draft", help="draft a TaskSpec JSON file from explicit inputs")
    draft_parser.add_argument("goal", help="desired project change")
    draft_parser.add_argument("--task-id", help="explicit TaskSpec id")
    draft_parser.add_argument("--title", help="short TaskSpec title")
    draft_parser.add_argument("--allow", action="append", default=[], help="writable file path; repeat for multiple files")
    draft_parser.add_argument("--read", action="append", default=[], help="read-only context file path; repeat for multiple files")
    draft_parser.add_argument("--test", action="append", default=[], help="verification command; repeat for multiple commands")
    draft_parser.add_argument("--constraint", action="append", default=[], help="implementation constraint; repeat for multiple constraints")
    draft_parser.add_argument("--expect", action="append", default=[], help="expected behavior; repeat for multiple expectations")
    draft_parser.add_argument("--context", action="append", default=[], help="context requirement note; repeat for multiple notes")
    draft_parser.add_argument("--max-attempts", type=int, default=3, help="retry budget for the drafted task")
    draft_parser.add_argument("--output", type=Path, help="write TaskSpec JSON to this path")
    draft_parser.add_argument("--json", action="store_true", help="print machine-readable draft output")
    draft_parser.set_defaults(handler=cmd_draft)

    refine_parser = subcommands.add_parser("refine", help="refine a TaskSpec with the configured Ollama model")
    refine_parser.add_argument("taskspec", type=Path)
    refine_parser.add_argument("--model", help="override configured Ollama model")
    refine_parser.add_argument("--ollama-binary", help="override configured Ollama executable path")
    refine_parser.add_argument("--ollama-host", help="override configured Ollama HTTP API host")
    refine_parser.add_argument("--timeout", type=int, help="Ollama timeout in seconds")
    refine_parser.add_argument("--output", type=Path, help="write refined TaskSpec JSON to this path")
    refine_parser.add_argument("--json", action="store_true", help="print machine-readable refine output")
    refine_parser.set_defaults(handler=cmd_refine)

    run_parser = subcommands.add_parser("run", help="run an APOS 0.1 task loop")
    run_parser.add_argument("taskspec", type=Path)
    run_parser.add_argument("--coder-command", help="override configured Local Coder command")
    run_parser.add_argument("--max-attempts", type=int, help="override retry budget")
    run_parser.add_argument("--timeout", type=int, help="command timeout in seconds")
    run_parser.add_argument("--no-commit", action="store_true", help="leave successful changes uncommitted")
    run_parser.add_argument("--allow-dirty", action="store_true", help="allow starting from a dirty worktree")
    run_parser.add_argument("--approve-read", action="append", default=[], help="pre-approve a requested read path; repeat for multiple paths")
    run_parser.add_argument("--approve-write", action="append", default=[], help="pre-approve a requested write path; repeat for multiple paths")
    run_parser.add_argument("--deny-permission", action="append", default=[], help="pre-deny a requested path; repeat for multiple paths")
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

    benchmark_run_parser = benchmark_subcommands.add_parser("run", help="run every TaskSpec in a benchmark suite")
    benchmark_run_parser.add_argument("suite", type=Path)
    benchmark_run_parser.add_argument("--coder-command", help="override configured Local Coder command")
    benchmark_run_parser.add_argument("--max-attempts", type=int, help="override retry budget for each task")
    benchmark_run_parser.add_argument("--timeout", type=int, help="command timeout in seconds")
    benchmark_run_parser.add_argument("--no-commit", action="store_true", help="leave successful task changes uncommitted")
    benchmark_run_parser.add_argument("--allow-dirty", action="store_true", help="allow starting tasks from a dirty worktree")
    benchmark_run_parser.add_argument("--keep-going", action="store_true", help="continue after a failed benchmark task")
    benchmark_run_parser.add_argument("--approve-read", action="append", default=[], help="pre-approve a requested read path for every task")
    benchmark_run_parser.add_argument("--approve-write", action="append", default=[], help="pre-approve a requested write path for every task")
    benchmark_run_parser.add_argument("--deny-permission", action="append", default=[], help="pre-deny a requested path for every task")
    benchmark_run_parser.add_argument("--json", action="store_true", help="print machine-readable benchmark result")
    benchmark_run_parser.set_defaults(handler=cmd_benchmark_run)

    benchmark_compare_parser = benchmark_subcommands.add_parser("compare", help="compare two or more benchmark results")
    benchmark_compare_parser.add_argument("results", nargs="+", help="benchmark result paths or result directories")
    benchmark_compare_parser.add_argument("--json", action="store_true", help="print machine-readable benchmark comparison")
    benchmark_compare_parser.set_defaults(handler=cmd_benchmark_compare)

    benchmark_results_parser = benchmark_subcommands.add_parser("results", help="inspect benchmark run results")
    benchmark_results_subcommands = benchmark_results_parser.add_subparsers(dest="benchmark_results_command")

    benchmark_results_list_parser = benchmark_results_subcommands.add_parser("list", help="list recent benchmark results")
    benchmark_results_list_parser.add_argument("--limit", type=int, default=20, help="maximum number of benchmark results to show")
    benchmark_results_list_parser.add_argument("--json", action="store_true", help="print machine-readable result list")
    benchmark_results_list_parser.set_defaults(handler=cmd_benchmark_results_list)

    benchmark_results_show_parser = benchmark_results_subcommands.add_parser("show", help="show one benchmark result")
    benchmark_results_show_parser.add_argument("result", help="benchmark result path or result directory")
    benchmark_results_show_parser.add_argument("--json", action="store_true", help="print machine-readable benchmark result")
    benchmark_results_show_parser.set_defaults(handler=cmd_benchmark_results_show)

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


def cmd_bootstrap(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    created = ensure_project_memory(root)
    config = load_config(root)
    if args.ollama_model:
        config.setdefault("local_coder", {})["command"] = _ollama_coder_command(args.ollama_model, args.ollama_binary)
        config.setdefault("ollama", {})["model"] = args.ollama_model
        config.setdefault("ollama", {})["binary"] = args.ollama_binary
        config.setdefault("ollama", {})["host"] = args.ollama_host
        save_config(root, config)

    print("APOS bootstrap complete.")
    print(f"Project: {root}")
    print(f"Memory files created: {len(created)}")
    command = configured_coder_command(root)
    print(f"Local Coder: {command or '<not configured>'}")
    if not command:
        print("Next: run apos connect-ollama --model <model> or apos connect --coder-command <command>")
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
    command = _ollama_coder_command(args.model, args.ollama_binary)
    root = GitClient(Path.cwd()).ensure_repo()
    ensure_project_memory(root)
    config = load_config(root)
    config.setdefault("local_coder", {})["command"] = command
    config.setdefault("ollama", {})["model"] = args.model
    config.setdefault("ollama", {})["binary"] = args.ollama_binary
    config.setdefault("ollama", {})["host"] = args.ollama_host
    save_config(root, config)
    print(f"Ollama Local Coder configured: {args.model}")
    return 0


def _ollama_coder_command(model: str, binary: str) -> str:
    return subprocess.list2cmdline(
        [
            sys.executable,
            "-m",
            "apos.ollama",
            "--model",
            model,
            "--ollama-binary",
            binary,
        ]
    )


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


def cmd_draft(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    spec = draft_task_spec(
        root=root,
        goal=args.goal,
        allowed_files=args.allow,
        read_only_files=args.read,
        test_commands=args.test,
        task_id=args.task_id,
        title=args.title,
        constraints=args.constraint or None,
        expected_behavior=args.expect or None,
        context_requirements=args.context or None,
        max_attempts=args.max_attempts,
    )
    if args.output:
        write_task_spec(root / args.output, spec)
        if args.json:
            print(json.dumps({"path": args.output.as_posix(), "task": spec.to_dict()}, indent=2, ensure_ascii=False))
            return 0
        print(f"TaskSpec written: {args.output}")
    else:
        print(json.dumps(spec.to_dict(), indent=2, ensure_ascii=False))
    return 0


def cmd_refine(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    configured_model, configured_binary, configured_host = configured_ollama(root)
    model = args.model or configured_model
    if not model:
        raise DraftError("no Ollama model configured; run apos connect-ollama or pass --model")
    binary = args.ollama_binary or configured_binary
    host = args.ollama_host or configured_host
    timeout = int(args.timeout or load_config(root).get("defaults", {}).get("command_timeout_seconds", 120))
    spec = TaskSpec.load(args.taskspec)
    refined = refine_task_spec_with_ollama(
        spec=spec,
        model=model,
        ollama_binary=binary,
        ollama_host=host,
        timeout_seconds=timeout,
    )
    if args.output:
        write_task_spec(root / args.output, refined)
        if args.json:
            print(json.dumps({"path": args.output.as_posix(), "task": refined.to_dict()}, indent=2, ensure_ascii=False))
            return 0
        print(f"Refined TaskSpec written: {args.output}")
    else:
        print(json.dumps(refined.to_dict(), indent=2, ensure_ascii=False))
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
            approved_read=tuple(args.approve_read),
            approved_write=tuple(args.approve_write),
            denied_permissions=tuple(args.deny_permission),
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


def cmd_benchmark_run(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    result = run_benchmark_suite(
        root,
        args.suite,
        BenchmarkRunOptions(
            coder_command=args.coder_command,
            max_attempts=args.max_attempts,
            no_commit=args.no_commit,
            allow_dirty=args.allow_dirty,
            command_timeout_seconds=args.timeout,
            keep_going=args.keep_going,
            approved_read=tuple(args.approve_read),
            approved_write=tuple(args.approve_write),
            denied_permissions=tuple(args.deny_permission),
        ),
    )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _print_benchmark_result(result)
    return 0 if result.get("status") == "PASS" else 2


def cmd_benchmark_compare(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    comparison = compare_benchmark_results(root, args.results)
    if args.json:
        print(json.dumps(comparison, indent=2, ensure_ascii=False))
        return 0
    _print_benchmark_comparison(comparison)
    return 0


def cmd_benchmark_results_list(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    entries = list_benchmark_results(root, limit=args.limit)
    if args.json:
        print(json.dumps([entry.to_dict() for entry in entries], indent=2, ensure_ascii=False))
        return 0
    if not entries:
        print("No APOS benchmark results found.")
        return 0
    for entry in entries:
        print(
            f"{entry.relative_path}  {entry.status}  {entry.suite_id}  "
            f"tasks={entry.passed_tasks}/{entry.total_tasks}  score={entry.average_quality_score}"
        )
    return 0


def cmd_benchmark_results_show(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    try:
        result = load_benchmark_result(root, args.result)
    except FileNotFoundError as exc:
        print(f"APOS error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    _print_benchmark_result(result)
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
    failure = report.get("failure") if isinstance(report.get("failure"), dict) else {}
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
    print(f"Failure: primary={failure.get('primary') or 'none'}, recovered={failure.get('recovered') or False}")
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


def _print_benchmark_result(result: dict[str, object]) -> None:
    suite = result.get("suite") if isinstance(result.get("suite"), dict) else {}
    runner_profile = result.get("runner_profile") if isinstance(result.get("runner_profile"), dict) else {}
    ollama = runner_profile.get("ollama") if isinstance(runner_profile.get("ollama"), dict) else {}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    tasks = result.get("tasks") if isinstance(result.get("tasks"), list) else []
    print(f"Benchmark result: {result.get('result_id')}")
    print(f"Suite: {suite.get('suite_id')}")
    print(f"Status: {result.get('status')}")
    if runner_profile:
        print(f"Runner: APOS {runner_profile.get('apos_version')}  model={ollama.get('model') or '-'}")
    print(f"Tasks: {summary.get('passed_tasks')}/{summary.get('total_tasks')} passed")
    print(f"Average score: {summary.get('average_quality_score')}")
    primary_failures = summary.get("primary_failures") if isinstance(summary.get("primary_failures"), dict) else {}
    if primary_failures:
        formatted = ", ".join(f"{key}={value}" for key, value in primary_failures.items())
        print(f"Primary failures: {formatted}")
    print(f"Result file: {result.get('result_path')}")
    for item in tasks:
        if not isinstance(item, dict):
            continue
        task = item.get("task") if isinstance(item.get("task"), dict) else {}
        report = item.get("report") if isinstance(item.get("report"), dict) else {}
        quality = report.get("quality") if isinstance(report.get("quality"), dict) else {}
        print(
            f"  {task.get('task_id')}  {item.get('status')}  "
            f"score={quality.get('score')}  duration={item.get('duration_seconds')}s  "
            f"log={item.get('run_log')}"
        )


def _print_benchmark_comparison(comparison: dict[str, object]) -> None:
    summary = comparison.get("summary") if isinstance(comparison.get("summary"), dict) else {}
    results = comparison.get("results") if isinstance(comparison.get("results"), list) else []
    print("Benchmark comparison")
    print(f"Results: {summary.get('result_count')}")
    if summary.get("best_result_id"):
        print(f"Best: {summary.get('best_result_id')}  suite={summary.get('best_suite_id')}  score={summary.get('best_score')}")
    for item in results:
        if not isinstance(item, dict):
            continue
        model = item.get("ollama_model") or "-"
        print(
            f"#{item.get('rank')}  {item.get('result_id')}  {item.get('status')}  "
            f"suite={item.get('suite_id')}  tasks={item.get('passed_tasks')}/{item.get('total_tasks')}  "
            f"score={item.get('average_quality_score')}  duration={item.get('total_duration_seconds')}s  "
            f"model={model}"
        )
        if item.get("result_path"):
            print(f"  {item.get('result_path')}")


if __name__ == "__main__":
    raise SystemExit(main())
