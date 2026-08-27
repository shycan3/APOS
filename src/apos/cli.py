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
from .core import (
    Actor,
    ActorKind,
    Capability,
    CommandPolicy,
    Decision,
    ProjectRuntime,
    StaticPermissionPolicy,
)
from .draft import DraftError, draft_task_spec, refine_task_spec_with_ollama, write_task_spec
from .evolution import (
    EvolutionError,
    create_candidate,
    evaluate_candidate,
    evolution_status,
    record_review,
    run_candidate,
    validate_evolution,
)
from .git import GitClient, GitError
from .kernel import Kernel, KernelError, RunOptions
from .models import SpecError, TaskSpec
from .report import generate_quality_report
from .runlog import list_run_logs, load_run_log


class KoreanArgumentParser(argparse.ArgumentParser):
    """Argument parser with Korean help headings and common error messages."""

    def __init__(self, *args, **kwargs) -> None:
        add_help = kwargs.pop("add_help", True)
        super().__init__(*args, add_help=False, **kwargs)
        if add_help:
            self.add_argument("-h", "--help", action="help", help="도움말을 표시하고 종료합니다")

    def format_help(self) -> str:
        return _translate_help(super().format_help())

    def format_usage(self) -> str:
        return _translate_help(super().format_usage())

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: 오류: {_translate_parser_error(message)}\n")


def _translate_help(value: str) -> str:
    replacements = {
        "usage:": "사용법:",
        "positional arguments:": "위치 인수:",
        "options:": "선택 사항:",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    return value


def _translate_parser_error(value: str) -> str:
    replacements = {
        "the following arguments are required:": "다음 인수가 필요합니다:",
        "unrecognized arguments:": "알 수 없는 인수:",
        "argument ": "인수 ",
        "invalid choice:": "잘못된 선택:",
        "choose from": "선택 가능:",
        "expected one argument": "하나의 값이 필요합니다",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    return value


STATUS_LABELS = {
    "ACTIVE": "활성",
    "AWAITING_REVIEWS": "검수 대기",
    "BLOCKED": "차단",
    "CREATED": "생성됨",
    "DEVELOPED": "개발 완료",
    "DEVELOPMENT_FAILED": "개발 실패",
    "FAIL": "실패",
    "FAILED": "실패",
    "INCOMPLETE": "미완료",
    "NEEDS_PERMISSION": "권한 필요",
    "NOT_READY": "준비 안 됨",
    "PASS": "통과",
    "PERMISSION_DENIED": "권한 거부",
    "PERMISSION_GRANTED": "권한 승인",
    "PROMOTABLE": "승격 가능",
    "READY_FOR_REVIEW": "검수 준비 완료",
    "REJECTED": "거절됨",
    "REVIEW_REJECTED": "검수 거절",
    "STALE_EVALUATION": "평가 만료",
    "UNKNOWN": "알 수 없음",
}

VERDICT_LABELS = {
    "blocked": "차단됨",
    "failed": "실패",
    "ready": "준비 완료",
    "usable": "사용 가능",
}

RESPONSE_LABELS = {
    "file_replacement": "파일 교체",
    "patch": "패치",
    "request_permission": "권한 요청",
}


def _status(value: object) -> str:
    code = str(value or "UNKNOWN")
    label = STATUS_LABELS.get(code)
    return f"{label}({code})" if label else code


def _yes_no(value: object) -> str:
    return "예" if bool(value) else "아니요"


def _labeled(value: object, labels: dict[str, str]) -> str:
    code = str(value or "-")
    label = labels.get(code)
    return f"{label}({code})" if label else code


def _message(value: object) -> str:
    message = str(value or "")
    exact = {
        "tests already passed before coder changes": "로컬 코더 변경 전에 테스트가 이미 통과했습니다.",
        "failed attempt patch was rolled back": "실패한 시도의 패치를 롤백했습니다.",
        "failed file replacement was rolled back": "실패한 파일 교체를 롤백했습니다.",
    }
    if message in exact:
        return exact[message]
    prefixes = {
        "tests passed after modifying ": "다음 파일 수정 후 테스트 통과: ",
        "tests passed after replacing ": "다음 파일 교체 후 테스트 통과: ",
        "Local Coder requested permission": "로컬 코더가 권한을 요청했습니다",
    }
    for source, target in prefixes.items():
        if message.startswith(source):
            return f"{target}{message.removeprefix(source)}"
    return message


def _report_note(value: object) -> str:
    note = str(value)
    if note == "Task completed successfully.":
        return "작업을 성공적으로 완료했습니다."
    if note == "No verification commands were recorded.":
        return "기록된 검증 명령이 없습니다."
    if note.startswith("Task ended with status "):
        return f"작업 종료 상태: {_status(note.removeprefix('Task ended with status ').rstrip('.'))}"
    if note.startswith("Required ") and note.endswith(" attempts."):
        count = note.removeprefix("Required ").removesuffix(" attempts.")
        return f"{count}회의 시도가 필요했습니다."
    if note.endswith(" verification command(s) failed."):
        count = note.removesuffix(" verification command(s) failed.")
        return f"검증 명령 {count}개가 실패했습니다."
    if note.endswith(" permission request(s) were raised."):
        count = note.removesuffix(" permission request(s) were raised.")
        return f"권한 요청 {count}개가 발생했습니다."
    if note.endswith(" rollback(s) failed."):
        count = note.removesuffix(" rollback(s) failed.")
        return f"롤백 {count}개가 실패했습니다."
    return note


def _configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _configure_console_encoding()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 0
    try:
        return int(args.handler(args))
    except (BenchmarkError, DraftError, EvolutionError, GitError, KernelError, SpecError) as exc:
        print(f"APOS 오류: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = KoreanArgumentParser(prog="apos", description="APOS 1.2 통제형 AI 개발 및 자기진화 실행 환경")
    parser.add_argument("--version", action="version", version=f"apos {__version__}", help="APOS 버전을 표시하고 종료합니다")

    subcommands = parser.add_subparsers(dest="command")

    init_parser = subcommands.add_parser("init", help="현재 Git 프로젝트에 APOS 메모리를 초기화합니다")
    init_parser.set_defaults(handler=cmd_init)

    bootstrap_parser = subcommands.add_parser("bootstrap", help="APOS를 초기화하고 선택적으로 Ollama를 설정합니다")
    bootstrap_parser.add_argument("--ollama-model", help="로컬 코더로 사용할 Ollama 모델")
    bootstrap_parser.add_argument("--ollama-binary", default="ollama", help="Ollama 실행 파일 경로")
    bootstrap_parser.add_argument("--ollama-host", default="http://127.0.0.1:11434", help="Ollama HTTP API 주소")
    bootstrap_parser.set_defaults(handler=cmd_bootstrap)

    connect_parser = subcommands.add_parser("connect", help="로컬 코더 명령을 설정합니다")
    connect_parser.add_argument("--coder-command", required=True, help="표준 입력으로 프롬프트를 받고 패치를 출력하는 명령")
    connect_parser.set_defaults(handler=cmd_connect)

    connect_ollama_parser = subcommands.add_parser("connect-ollama", help="Ollama를 로컬 코더로 설정합니다")
    connect_ollama_parser.add_argument("--model", required=True, help="Ollama 모델 이름 (예: qwen2.5-coder:7b)")
    connect_ollama_parser.add_argument("--ollama-binary", default="ollama", help="Ollama 실행 파일 경로")
    connect_ollama_parser.add_argument("--ollama-host", default="http://127.0.0.1:11434", help="Ollama HTTP API 주소")
    connect_ollama_parser.set_defaults(handler=cmd_connect_ollama)

    status_parser = subcommands.add_parser("status", help="APOS 및 Git 프로젝트 상태를 표시합니다")
    status_parser.set_defaults(handler=cmd_status)

    validate_parser = subcommands.add_parser("validate", help="TaskSpec JSON 파일을 검증합니다")
    validate_parser.add_argument("taskspec", type=Path)
    validate_parser.set_defaults(handler=cmd_validate)

    template_parser = subcommands.add_parser("task-template", help="최소 TaskSpec 템플릿을 출력합니다")
    template_parser.set_defaults(handler=cmd_task_template)

    draft_parser = subcommands.add_parser("draft", help="명시한 입력으로 TaskSpec JSON 초안을 만듭니다")
    draft_parser.add_argument("goal", help="원하는 프로젝트 변경 목표")
    draft_parser.add_argument("--task-id", help="지정할 TaskSpec ID")
    draft_parser.add_argument("--title", help="짧은 TaskSpec 제목")
    draft_parser.add_argument("--allow", action="append", default=[], help="쓰기 허용 파일 경로 (여러 번 지정 가능)")
    draft_parser.add_argument("--read", action="append", default=[], help="읽기 전용 참고 파일 경로 (여러 번 지정 가능)")
    draft_parser.add_argument("--test", action="append", default=[], help="검증 명령 (여러 번 지정 가능)")
    draft_parser.add_argument("--constraint", action="append", default=[], help="구현 제약 조건 (여러 번 지정 가능)")
    draft_parser.add_argument("--expect", action="append", default=[], help="기대 동작 (여러 번 지정 가능)")
    draft_parser.add_argument("--context", action="append", default=[], help="컨텍스트 요구 사항 (여러 번 지정 가능)")
    draft_parser.add_argument("--max-attempts", type=int, default=3, help="초안 작업의 최대 재시도 횟수")
    draft_parser.add_argument("--output", type=Path, help="TaskSpec JSON을 저장할 경로")
    draft_parser.add_argument("--json", action="store_true", help="기계 판독용 JSON으로 출력합니다")
    draft_parser.set_defaults(handler=cmd_draft)

    refine_parser = subcommands.add_parser("refine", help="설정된 Ollama 모델로 TaskSpec을 다듬습니다")
    refine_parser.add_argument("taskspec", type=Path)
    refine_parser.add_argument("--model", help="설정된 Ollama 모델을 덮어씁니다")
    refine_parser.add_argument("--ollama-binary", help="설정된 Ollama 실행 파일 경로를 덮어씁니다")
    refine_parser.add_argument("--ollama-host", help="설정된 Ollama HTTP API 주소를 덮어씁니다")
    refine_parser.add_argument("--timeout", type=int, help="Ollama 제한 시간(초)")
    refine_parser.add_argument("--output", type=Path, help="다듬은 TaskSpec JSON을 저장할 경로")
    refine_parser.add_argument("--json", action="store_true", help="기계 판독용 JSON으로 출력합니다")
    refine_parser.set_defaults(handler=cmd_refine)

    run_parser = subcommands.add_parser("run", help="APOS 작업 루프를 실행합니다")
    run_parser.add_argument("taskspec", type=Path)
    run_parser.add_argument("--coder-command", help="설정된 로컬 코더 명령을 덮어씁니다")
    run_parser.add_argument("--max-attempts", type=int, help="최대 재시도 횟수를 덮어씁니다")
    run_parser.add_argument("--timeout", type=int, help="명령 제한 시간(초)")
    run_parser.add_argument("--no-commit", action="store_true", help="성공한 변경을 커밋하지 않습니다")
    run_parser.add_argument("--allow-dirty", action="store_true", help="변경이 남은 작업공간에서 시작하도록 허용합니다")
    run_parser.add_argument("--approve-read", action="append", default=[], help="요청할 읽기 경로를 미리 승인합니다")
    run_parser.add_argument("--approve-write", action="append", default=[], help="요청할 쓰기 경로를 미리 승인합니다")
    run_parser.add_argument("--deny-permission", action="append", default=[], help="요청할 경로 권한을 미리 거부합니다")
    run_parser.add_argument("--json", action="store_true", help="기계 판독용 실행 요약을 출력합니다")
    run_parser.set_defaults(handler=cmd_run)

    runs_parser = subcommands.add_parser("runs", help="APOS 실행 기록을 조회합니다")
    runs_subcommands = runs_parser.add_subparsers(dest="runs_command")

    runs_list_parser = runs_subcommands.add_parser("list", help="최근 APOS 실행 목록을 표시합니다")
    runs_list_parser.add_argument("--limit", type=int, default=20, help="표시할 최대 실행 수")
    runs_list_parser.add_argument("--json", action="store_true", help="기계 판독용 실행 목록을 출력합니다")
    runs_list_parser.set_defaults(handler=cmd_runs_list)

    runs_show_parser = runs_subcommands.add_parser("show", help="APOS 실행 기록 하나를 표시합니다")
    runs_show_parser.add_argument("run_log", help="실행 기록 경로 (예: .apos/runs/task-001/<run-id>)")
    runs_show_parser.add_argument("--json", action="store_true", help="기계 판독용 실행 상세를 출력합니다")
    runs_show_parser.set_defaults(handler=cmd_runs_show)

    report_parser = subcommands.add_parser("report", help="APOS 실행의 간결한 품질 보고서를 생성합니다")
    report_parser.add_argument("run_log", help="실행 기록 경로 (예: .apos/runs/task-001/<run-id>)")
    report_parser.add_argument("--json", action="store_true", help="기계 판독용 품질 보고서를 출력합니다")
    report_parser.set_defaults(handler=cmd_report)

    benchmark_parser = subcommands.add_parser("benchmark", help="벤치마크 작업 모음을 실행하거나 조회합니다")
    benchmark_subcommands = benchmark_parser.add_subparsers(dest="benchmark_command")

    benchmark_validate_parser = benchmark_subcommands.add_parser("validate", help="벤치마크 모음을 검증합니다")
    benchmark_validate_parser.add_argument("suite", type=Path)
    benchmark_validate_parser.set_defaults(handler=cmd_benchmark_validate)

    benchmark_show_parser = benchmark_subcommands.add_parser("show", help="벤치마크 모음 정보를 표시합니다")
    benchmark_show_parser.add_argument("suite", type=Path)
    benchmark_show_parser.add_argument("--json", action="store_true", help="기계 판독용 모음 정보를 출력합니다")
    benchmark_show_parser.set_defaults(handler=cmd_benchmark_show)

    benchmark_run_parser = benchmark_subcommands.add_parser("run", help="벤치마크 모음의 모든 TaskSpec을 실행합니다")
    benchmark_run_parser.add_argument("suite", type=Path)
    benchmark_run_parser.add_argument("--coder-command", help="설정된 로컬 코더 명령을 덮어씁니다")
    benchmark_run_parser.add_argument("--max-attempts", type=int, help="각 작업의 최대 재시도 횟수를 덮어씁니다")
    benchmark_run_parser.add_argument("--timeout", type=int, help="명령 제한 시간(초)")
    benchmark_run_parser.add_argument("--no-commit", action="store_true", help="성공한 작업 변경을 커밋하지 않습니다")
    benchmark_run_parser.add_argument("--allow-dirty", action="store_true", help="변경이 남은 작업공간에서 시작하도록 허용합니다")
    benchmark_run_parser.add_argument("--keep-going", action="store_true", help="벤치마크 작업이 실패해도 계속합니다")
    benchmark_run_parser.add_argument("--approve-read", action="append", default=[], help="모든 작업의 읽기 경로를 미리 승인합니다")
    benchmark_run_parser.add_argument("--approve-write", action="append", default=[], help="모든 작업의 쓰기 경로를 미리 승인합니다")
    benchmark_run_parser.add_argument("--deny-permission", action="append", default=[], help="모든 작업의 경로 권한을 미리 거부합니다")
    benchmark_run_parser.add_argument("--json", action="store_true", help="기계 판독용 벤치마크 결과를 출력합니다")
    benchmark_run_parser.set_defaults(handler=cmd_benchmark_run)

    benchmark_compare_parser = benchmark_subcommands.add_parser("compare", help="두 개 이상의 벤치마크 결과를 비교합니다")
    benchmark_compare_parser.add_argument("results", nargs="+", help="벤치마크 결과 파일 또는 디렉터리 경로")
    benchmark_compare_parser.add_argument("--json", action="store_true", help="기계 판독용 비교 결과를 출력합니다")
    benchmark_compare_parser.set_defaults(handler=cmd_benchmark_compare)

    benchmark_results_parser = benchmark_subcommands.add_parser("results", help="벤치마크 실행 결과를 조회합니다")
    benchmark_results_subcommands = benchmark_results_parser.add_subparsers(dest="benchmark_results_command")

    benchmark_results_list_parser = benchmark_results_subcommands.add_parser("list", help="최근 벤치마크 결과를 표시합니다")
    benchmark_results_list_parser.add_argument("--limit", type=int, default=20, help="표시할 최대 결과 수")
    benchmark_results_list_parser.add_argument("--json", action="store_true", help="기계 판독용 결과 목록을 출력합니다")
    benchmark_results_list_parser.set_defaults(handler=cmd_benchmark_results_list)

    benchmark_results_show_parser = benchmark_results_subcommands.add_parser("show", help="벤치마크 결과 하나를 표시합니다")
    benchmark_results_show_parser.add_argument("result", help="벤치마크 결과 파일 또는 디렉터리 경로")
    benchmark_results_show_parser.add_argument("--json", action="store_true", help="기계 판독용 벤치마크 결과를 출력합니다")
    benchmark_results_show_parser.set_defaults(handler=cmd_benchmark_results_show)

    evolution_parser = subcommands.add_parser(
        "evolution",
        aliases=["evolve"],
        help="격리된 APOS 진화 후보를 생성하고 통제합니다",
    )
    evolution_parser.set_defaults(handler=cmd_evolution_orchestrator)
    evolution_subcommands = evolution_parser.add_subparsers(dest="evolution_command")

    evolution_validate_parser = evolution_subcommands.add_parser("validate", help="고정된 1.1 진화 정책을 검증합니다")
    evolution_validate_parser.add_argument("--json", action="store_true", help="기계 판독용 검증 증거를 출력합니다")
    evolution_validate_parser.set_defaults(handler=cmd_evolution_validate)

    evolution_status_parser = evolution_subcommands.add_parser("status", help="기준선 또는 후보 통제 상태를 표시합니다")
    evolution_status_parser.add_argument("candidate_id", nargs="?", help="선택할 후보 ID")
    evolution_status_parser.add_argument("--json", action="store_true", help="기계 판독용 상태를 출력합니다")
    evolution_status_parser.set_defaults(handler=cmd_evolution_status)

    evolution_create_parser = evolution_subcommands.add_parser("create", help="격리된 후보 worktree를 생성합니다")
    evolution_create_parser.add_argument("proposal", type=Path, help="진화 제안서 JSON 파일")
    evolution_create_parser.add_argument("--candidate-id", help="안정적인 영문 소문자 후보 ID")
    evolution_create_parser.add_argument("--base-ref", help="검수된 상위 릴리스 ref (기본값: 제안서 또는 v1.1.0)")
    evolution_create_parser.add_argument("--json", action="store_true", help="기계 판독용 후보 정보를 출력합니다")
    evolution_create_parser.set_defaults(handler=cmd_evolution_create)

    evolution_run_parser = evolution_subcommands.add_parser("run", help="APOS가 격리 후보를 개발하도록 실행합니다")
    evolution_run_parser.add_argument("candidate_id")
    evolution_run_parser.add_argument("--coder-command", help="설정된 로컬 코더 명령을 덮어씁니다")
    evolution_run_parser.add_argument("--max-attempts", type=int, help="제안서의 최대 재시도 횟수를 덮어씁니다")
    evolution_run_parser.add_argument("--timeout", type=int, help="명령 제한 시간(초)")
    evolution_run_parser.add_argument("--no-commit", action="store_true", help="성공한 변경을 커밋하지 않습니다")
    evolution_run_parser.add_argument("--approve-read", action="append", default=[], help="요청할 읽기 경로를 미리 승인합니다")
    evolution_run_parser.add_argument("--approve-write", action="append", default=[], help="요청할 쓰기 경로를 미리 승인합니다")
    evolution_run_parser.add_argument("--deny-permission", action="append", default=[], help="요청할 경로 권한을 미리 거부합니다")
    evolution_run_parser.add_argument("--json", action="store_true", help="기계 판독용 개발 결과를 출력합니다")
    evolution_run_parser.set_defaults(handler=cmd_evolution_run)

    evolution_evaluate_parser = evolution_subcommands.add_parser("evaluate", help="신뢰된 1.1 기준으로 후보를 평가합니다")
    evolution_evaluate_parser.add_argument("candidate_id")
    evolution_evaluate_parser.add_argument("--quick", action="store_true", help="벤치마크 없이 구조 및 단위 테스트만 실행합니다")
    evolution_evaluate_parser.add_argument("--timeout", type=int, default=600, help="각 검증 명령의 제한 시간(초)")
    evolution_evaluate_parser.add_argument("--json", action="store_true", help="기계 판독용 평가 보고서를 출력합니다")
    evolution_evaluate_parser.set_defaults(handler=cmd_evolution_evaluate)

    evolution_review_parser = evolution_subcommands.add_parser("review", help="커밋에 연결된 Codex 또는 사용자 검수를 기록합니다")
    evolution_review_parser.add_argument("candidate_id")
    evolution_review_parser.add_argument("--reviewer", required=True, choices=["codex", "human"])
    evolution_review_parser.add_argument("--decision", required=True, choices=["approve", "reject"])
    evolution_review_parser.add_argument("--note", required=True, help="검수 근거 또는 거절 사유")
    evolution_review_parser.add_argument("--json", action="store_true", help="기계 판독용 검수 기록을 출력합니다")
    evolution_review_parser.set_defaults(handler=cmd_evolution_review)

    return parser


def cmd_init(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    created = ensure_project_memory(root)
    if created:
        print("APOS 초기화 완료:")
        for path in created:
            print(f"  + {path.relative_to(root)}")
    else:
        print("APOS가 이미 초기화되어 있습니다.")
    return 0


def cmd_bootstrap(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    created = ensure_project_memory(root)
    config = load_config(root)
    if args.ollama_model:
        config.setdefault("local_coder", {})["command"] = _ollama_coder_command(args.ollama_model, args.ollama_binary, args.ollama_host)
        config.setdefault("ollama", {})["model"] = args.ollama_model
        config.setdefault("ollama", {})["binary"] = args.ollama_binary
        config.setdefault("ollama", {})["host"] = args.ollama_host
        save_config(root, config)

    print("APOS 부트스트랩 완료.")
    print(f"프로젝트: {root}")
    print(f"생성된 메모리 파일: {len(created)}개")
    command = configured_coder_command(root)
    print(f"로컬 코더: {command or '<설정되지 않음>'}")
    if not command:
        print("다음 단계: apos connect-ollama --model <모델> 또는 apos connect --coder-command <명령>을 실행하세요.")
    return 0


def cmd_connect(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    ensure_project_memory(root)
    config = load_config(root)
    config.setdefault("local_coder", {})["command"] = args.coder_command
    save_config(root, config)
    print("로컬 코더 명령을 설정했습니다.")
    return 0


def cmd_connect_ollama(args: argparse.Namespace) -> int:
    command = _ollama_coder_command(args.model, args.ollama_binary, args.ollama_host)
    root = GitClient(Path.cwd()).ensure_repo()
    ensure_project_memory(root)
    config = load_config(root)
    config.setdefault("local_coder", {})["command"] = command
    config.setdefault("ollama", {})["model"] = args.model
    config.setdefault("ollama", {})["binary"] = args.ollama_binary
    config.setdefault("ollama", {})["host"] = args.ollama_host
    save_config(root, config)
    print(f"Ollama 로컬 코더 설정 완료: {args.model}")
    return 0


def _ollama_coder_command(model: str, binary: str, host: str) -> str:
    return subprocess.list2cmdline(
        [
            sys.executable,
            "-m",
            "apos.ollama",
            "--model",
            model,
            "--ollama-binary",
            binary,
            "--ollama-host",
            host,
        ]
    )


def cmd_status(args: argparse.Namespace) -> int:
    top = GitClient(Path.cwd()).ensure_repo()
    root = top
    git = GitClient(root)
    config = load_config(root)
    print(f"APOS {__version__}")
    print(f"프로젝트: {top}")
    print(f"브랜치: {git.current_branch()}")
    command = config.get("local_coder", {}).get("command") or "<not configured>"
    print(f"로컬 코더: {command}")
    status = git.status_porcelain().strip()
    if status:
        print("Git 상태:")
        print(status)
    else:
        print("Git 상태: 깨끗함")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    runtime = ProjectRuntime.create(
        Path.cwd(),
        permission_policy=StaticPermissionPolicy(
            {Capability.PROJECT_READ: Decision.ALLOW},
            policy_id="cli-validate-read-only-v1",
        ),
        command_policy=CommandPolicy.current_python(),
    )
    result = runtime.validation.validate(
        str(args.taskspec),
        actor=Actor(ActorKind.USER, "local-cli"),
    )
    if not result.success:
        assert result.error is not None
        raise SpecError(f"{result.error.code.value}: {result.error.message}")

    assert result.data is not None
    print(f"TaskSpec 검증 완료: {result.data['task_id']} ({result.data['title']})")
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
        print(f"TaskSpec 저장 완료: {args.output}")
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
        print(f"다듬은 TaskSpec 저장 완료: {args.output}")
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
        print("APOS 실행 기록이 없습니다.")
        return 0

    for entry in entries:
        commit = entry.commit_hash if entry.committed and entry.commit_hash else "-"
        print(f"{entry.relative_path}  {_status(entry.status)}  {entry.task_id}  시도={entry.attempts}  커밋={commit}")
    return 0


def cmd_runs_show(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    try:
        detail = load_run_log(root, args.run_log)
    except FileNotFoundError as exc:
        print(f"APOS 오류: {exc}", file=sys.stderr)
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
        print(f"APOS 오류: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    _print_quality_report(report)
    return 0


def cmd_benchmark_validate(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    suite = validate_benchmark_suite(root, args.suite)
    print(f"벤치마크 모음 검증 완료: {suite.suite_id} (작업 {len(suite.tasks)}개)")
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
        print("APOS 벤치마크 결과가 없습니다.")
        return 0
    for entry in entries:
        print(
            f"{entry.relative_path}  {_status(entry.status)}  {entry.suite_id}  "
            f"작업={entry.passed_tasks}/{entry.total_tasks}  점수={entry.average_quality_score}"
        )
    return 0


def cmd_benchmark_results_show(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    try:
        result = load_benchmark_result(root, args.result)
    except FileNotFoundError as exc:
        print(f"APOS 오류: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    _print_benchmark_result(result)
    return 0


def cmd_evolution_orchestrator(args: argparse.Namespace) -> int:
    if getattr(args, "evolution_command", None) is None:
        if sys.stdin.isatty():
            from .orchestrator import run_orchestrator
            return run_orchestrator()
        root = GitClient(Path.cwd()).ensure_repo()
        result = evolution_status(root)
        _print_evolution_status(result)
        return 0
    return 0


def cmd_evolution_validate(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    result = validate_evolution(root)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    print("APOS 진화 정책 검증 완료.")
    print(f"기준선: {result['baseline_ref']} ({result['baseline_version']})")
    print(f"커밋: {result['baseline_commit']}")
    print(f"정책 해시: {result['policy_hash']}")
    print(f"버전 상한: < {result['maximum_version_exclusive']}")
    print(f"필수 검수자: {', '.join(result['required_reviewers'])}")
    print("자동 승격: 비활성화")
    return 0


def cmd_evolution_status(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    result = evolution_status(root, args.candidate_id)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    _print_evolution_status(result)
    return 0


def cmd_evolution_create(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    result = create_candidate(root, args.proposal, args.candidate_id, args.base_ref)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    print(f"진화 후보 생성 완료: {result['candidate_id']}")
    print(f"작업공간: {result['workspace']}")
    print(f"브랜치: {result['branch']}")
    print(f"상위 버전: {result['parent_ref']} ({result['parent_commit']})")
    print(f"목표 버전: {result['target_version']}")
    print("다음 단계: `apos evolution run <후보 ID>`로 격리 후보 개발을 실행하세요.")
    return 0


def cmd_evolution_run(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    result = run_candidate(
        root,
        args.candidate_id,
        RunOptions(
            coder_command=args.coder_command,
            max_attempts=args.max_attempts,
            no_commit=args.no_commit,
            command_timeout_seconds=args.timeout,
            approved_read=tuple(args.approve_read),
            approved_write=tuple(args.approve_write),
            denied_permissions=tuple(args.deny_permission),
        ),
    )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        run = result.get("run") if isinstance(result.get("run"), dict) else {}
        print(f"진화 후보: {args.candidate_id}")
        print(f"개발 상태: {_status(run.get('status'))}")
        print(f"후보 브랜치: {run.get('branch')}")
        print(f"커밋: {run.get('commit_hash') or '생략됨'}")
        print(f"실행 기록: {run.get('run_log') or '-'}")
    run = result.get("run") if isinstance(result.get("run"), dict) else {}
    return 0 if run.get("status") == "PASS" else 2


def cmd_evolution_evaluate(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    result = evaluate_candidate(root, args.candidate_id, quick=args.quick, timeout_seconds=args.timeout)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _print_evolution_evaluation(result)
    return 0 if result.get("status") in {"READY_FOR_REVIEW", "INCOMPLETE"} else 2


def cmd_evolution_review(args: argparse.Namespace) -> int:
    root = GitClient(Path.cwd()).ensure_repo()
    result = record_review(root, args.candidate_id, args.reviewer, args.decision, args.note)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    review = result.get("review") if isinstance(result.get("review"), dict) else {}
    promotion = result.get("promotion") if isinstance(result.get("promotion"), dict) else {}
    print(f"검수 기록 완료: {review.get('reviewer')} -> {review.get('decision')}")
    print(f"후보 커밋: {review.get('candidate_commit')}")
    print(f"승격 상태: {_status(promotion.get('status'))}")
    print("자동 승격: 비활성화")
    return 0


def _print_summary(summary) -> None:
    print(f"작업: {summary.task_id}")
    print(f"상태: {_status(summary.status)}")
    print(f"브랜치: {summary.branch}")
    for attempt in summary.attempts:
        print(f"시도 {attempt.attempt}: {_status(attempt.status)}")
        print(f"  {_message(attempt.message)}")
        for result in attempt.test_results:
            print(f"  테스트: {result.command} -> {_status(result.status)} ({result.exit_code})")
    if summary.committed:
        print(f"커밋: {summary.commit_hash}")
    elif summary.status == "PASS":
        print("커밋: 생략됨")
    if summary.run_log:
        print(f"실행 기록: {summary.run_log}")


def _print_run_log(detail: dict[str, object]) -> None:
    summary = detail.get("summary") if isinstance(detail.get("summary"), dict) else {}
    run = detail.get("run") if isinstance(detail.get("run"), dict) else {}
    attempts = detail.get("attempts") if isinstance(detail.get("attempts"), list) else []

    print(f"실행 기록: {detail.get('path')}")
    print(f"작업: {summary.get('task_id') or run.get('task_id')}")
    print(f"제목: {run.get('title') or '-'}")
    print(f"상태: {_status(summary.get('status'))}")
    print(f"브랜치: {summary.get('branch') or run.get('branch')}")
    print(f"시작 시각: {run.get('started_at') or '-'}")
    commit_hash = summary.get("commit_hash")
    if commit_hash:
        print(f"커밋: {commit_hash}")

    for item in attempts:
        if not isinstance(item, dict):
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        response = item.get("response") if isinstance(item.get("response"), dict) else {}
        tests = item.get("tests") if isinstance(item.get("tests"), list) else []
        rollback = item.get("rollback") if isinstance(item.get("rollback"), dict) else None
        print(f"시도 {result.get('attempt') or item.get('attempt')}: {_status(result.get('status'))}")
        message = result.get("message")
        if message:
            print(f"  {_message(message)}")
        response_type = response.get("type")
        if response_type:
            print(f"  응답: {_labeled(response_type, RESPONSE_LABELS)}")
        for test in tests:
            if not isinstance(test, dict):
                continue
            print(f"  테스트: {test.get('command')} -> {_status(test.get('status'))} ({test.get('exit_code')})")
        if rollback:
            print(f"  롤백: {_status(rollback.get('status'))} ({rollback.get('message')})")
        if item.get("patch_file"):
            print(f"  패치: {item.get('patch_file')}")


def _print_quality_report(report: dict[str, object]) -> None:
    quality = report.get("quality") if isinstance(report.get("quality"), dict) else {}
    tests = report.get("tests") if isinstance(report.get("tests"), dict) else {}
    responses = report.get("responses") if isinstance(report.get("responses"), dict) else {}
    rollbacks = report.get("rollbacks") if isinstance(report.get("rollbacks"), dict) else {}
    failure = report.get("failure") if isinstance(report.get("failure"), dict) else {}
    notes = quality.get("notes") if isinstance(quality.get("notes"), list) else []

    print(f"품질 보고서: {report.get('run_log')}")
    print(f"작업: {report.get('task_id')}")
    print(f"상태: {_status(report.get('status'))}")
    print(f"판정: {_labeled(quality.get('verdict'), VERDICT_LABELS)}")
    print(f"점수: {quality.get('score')}")
    print(f"시도 횟수: {report.get('attempts')}")
    print(f"테스트: {tests.get('passed')}/{tests.get('total')}개 통과, {tests.get('failed')}개 실패")
    print(
        f"응답: 패치={responses.get('patch')}, "
        f"file_replacement={responses.get('file_replacement')}, "
        f"권한요청={responses.get('permission_requests')}"
    )
    print(f"롤백: 통과={rollbacks.get('passed')}, 실패={rollbacks.get('failed')}")
    print(f"실패 정보: 주요={failure.get('primary') or '없음'}, 복구={_yes_no(failure.get('recovered'))}")
    if report.get("commit_hash"):
        print(f"커밋: {report.get('commit_hash')}")
    for note in notes:
        print(f"- {_report_note(note)}")


def _print_benchmark_suite(suite: dict[str, object]) -> None:
    tasks = suite.get("tasks") if isinstance(suite.get("tasks"), list) else []
    metrics = suite.get("metrics") if isinstance(suite.get("metrics"), list) else []
    print(f"벤치마크 모음: {suite.get('suite_id')}")
    print(f"제목: {suite.get('title')}")
    print(f"버전: {suite.get('version')}")
    description = suite.get("description")
    if description:
        print(f"설명: {description}")
    print(f"작업 수: {len(tasks)}")
    for task in tasks:
        if not isinstance(task, dict):
            continue
        tags = task.get("tags") if isinstance(task.get("tags"), list) else []
        tag_text = ",".join(str(tag) for tag in tags) if tags else "-"
        print(
            f"  {task.get('task_id')}  {task.get('path')}  "
            f"분류={task.get('category')}  난이도={task.get('difficulty')}  "
            f"가중치={task.get('weight')}  태그={tag_text}"
        )
    if metrics:
        print(f"측정 항목: {', '.join(str(metric) for metric in metrics)}")


def _print_benchmark_result(result: dict[str, object]) -> None:
    suite = result.get("suite") if isinstance(result.get("suite"), dict) else {}
    runner_profile = result.get("runner_profile") if isinstance(result.get("runner_profile"), dict) else {}
    ollama = runner_profile.get("ollama") if isinstance(runner_profile.get("ollama"), dict) else {}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    tasks = result.get("tasks") if isinstance(result.get("tasks"), list) else []
    print(f"벤치마크 결과: {result.get('result_id')}")
    print(f"모음: {suite.get('suite_id')}")
    print(f"상태: {_status(result.get('status'))}")
    if runner_profile:
        print(f"실행 환경: APOS {runner_profile.get('apos_version')}  모델={ollama.get('model') or '-'}")
    print(f"작업: {summary.get('passed_tasks')}/{summary.get('total_tasks')}개 통과")
    print(f"평균 점수: {summary.get('average_quality_score')}")
    primary_failures = summary.get("primary_failures") if isinstance(summary.get("primary_failures"), dict) else {}
    if primary_failures:
        formatted = ", ".join(f"{key}={value}" for key, value in primary_failures.items())
        print(f"주요 실패: {formatted}")
    print(f"결과 파일: {result.get('result_path')}")
    for item in tasks:
        if not isinstance(item, dict):
            continue
        task = item.get("task") if isinstance(item.get("task"), dict) else {}
        report = item.get("report") if isinstance(item.get("report"), dict) else {}
        quality = report.get("quality") if isinstance(report.get("quality"), dict) else {}
        print(
            f"  {task.get('task_id')}  {_status(item.get('status'))}  "
            f"점수={quality.get('score')}  소요={item.get('duration_seconds')}초  "
            f"기록={item.get('run_log')}"
        )


def _print_benchmark_comparison(comparison: dict[str, object]) -> None:
    summary = comparison.get("summary") if isinstance(comparison.get("summary"), dict) else {}
    results = comparison.get("results") if isinstance(comparison.get("results"), list) else []
    print("벤치마크 비교")
    print(f"결과 수: {summary.get('result_count')}")
    if summary.get("best_result_id"):
        print(f"최고 결과: {summary.get('best_result_id')}  모음={summary.get('best_suite_id')}  점수={summary.get('best_score')}")
    for item in results:
        if not isinstance(item, dict):
            continue
        model = item.get("ollama_model") or "-"
        print(
            f"#{item.get('rank')}  {item.get('result_id')}  {_status(item.get('status'))}  "
            f"모음={item.get('suite_id')}  작업={item.get('passed_tasks')}/{item.get('total_tasks')}  "
            f"점수={item.get('average_quality_score')}  소요={item.get('total_duration_seconds')}초  "
            f"모델={model}"
        )
        if item.get("result_path"):
            print(f"  {item.get('result_path')}")


def _print_evolution_status(result: dict[str, object]) -> None:
    baseline = result.get("baseline") if isinstance(result.get("baseline"), dict) else {}
    current = result.get("current") if isinstance(result.get("current"), dict) else {}
    governance = result.get("governance") if isinstance(result.get("governance"), dict) else {}
    print(f"진화 상태: {_status(result.get('status'))}")
    print(f"기준선: {baseline.get('ref')}  버전={baseline.get('version')}  커밋={baseline.get('commit')}")
    print(f"현재: {current.get('branch')}  버전={current.get('version')}  커밋={current.get('commit')}")
    print(f"정책 해시: {result.get('policy_hash')}")
    print(f"필수 검수자: {', '.join(str(item) for item in governance.get('required_reviewers', []))}")
    print("자동 승격: 비활성화")
    candidate = result.get("candidate") if isinstance(result.get("candidate"), dict) else None
    if candidate:
        promotion = result.get("promotion") if isinstance(result.get("promotion"), dict) else {}
        print(f"후보: {candidate.get('candidate_id')}  상태={_status(candidate.get('status'))}  목표={candidate.get('target_version')}")
        print(f"승격 상태: {_status(promotion.get('status'))}")
        missing = promotion.get("missing_reviewers") if isinstance(promotion.get("missing_reviewers"), list) else []
        if missing:
            print(f"남은 검수: {', '.join(str(item) for item in missing)}")
        return
    candidates = result.get("candidates") if isinstance(result.get("candidates"), list) else []
    print(f"후보 수: {len(candidates)}")
    for item in candidates:
        if isinstance(item, dict):
            print(f"  {item.get('candidate_id')}  {_status(item.get('status'))}  목표={item.get('target_version')}")


def _print_evolution_evaluation(result: dict[str, object]) -> None:
    print(f"진화 평가: {result.get('candidate_id')}")
    print(f"상태: {_status(result.get('status'))}")
    print(f"커밋: {result.get('candidate_commit')}")
    print(f"버전: {result.get('candidate_version')}")
    gates = result.get("gates") if isinstance(result.get("gates"), list) else []
    for gate in gates:
        if isinstance(gate, dict):
            print(f"  {_status(gate.get('status'))}  {gate.get('name')}: {gate.get('detail')}")
    print(f"증거: {result.get('report_path')}")
    print(f"검수 자료: {result.get('review_path')}")
    print("자동 승격: 비활성화")


if __name__ == "__main__":
    raise SystemExit(main())
