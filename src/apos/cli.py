from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from . import __version__
from .config import ensure_project_memory, load_config, save_config
from .git import GitClient, GitError
from .kernel import Kernel, KernelError, RunOptions
from .models import SpecError, TaskSpec


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 0
    try:
        return int(args.handler(args))
    except (GitError, KernelError, SpecError) as exc:
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


if __name__ == "__main__":
    raise SystemExit(main())
