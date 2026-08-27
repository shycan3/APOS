from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Callable

from . import __version__
from .config import configured_coder_command, configured_ollama, ensure_project_memory, load_config
from .evolution import (
    EvolutionError,
    EvolutionPolicy,
    candidate_promotion_status,
    create_candidate,
    evaluate_candidate,
    evolution_status,
    list_candidates,
    load_candidate,
    record_review,
    run_candidate,
    validate_evolution,
)
from .git import GitClient, GitError
from .kernel import RunOptions


STATUS_LABELS: dict[str, str] = {
    "ACTIVE": "활성",
    "AWAITING_REVIEWS": "검수 대기",
    "BLOCKED": "차단됨",
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


def format_status(value: object) -> str:
    code = str(value or "UNKNOWN")
    label = STATUS_LABELS.get(code)
    return f"{label}({code})" if label else code


def get_project_summary(root: Path) -> dict[str, object]:
    """Gather current APOS and evolution runtime status."""
    ensure_project_memory(root)
    git = GitClient(root)
    clean_repo = not git.status_porcelain().strip()
    branch = git.current_branch()
    config = load_config(root)
    coder_cmd = configured_coder_command(root)
    ollama_model, _, _ = configured_ollama(root)

    evo_status: dict[str, object] | None = None
    policy_valid = False
    try:
        evo_status = evolution_status(root)
        policy_valid = evo_status.get("status") == "ACTIVE"
    except Exception:
        pass

    candidates = list_candidates(root)

    return {
        "root": root,
        "branch": branch,
        "is_clean": clean_repo,
        "coder_cmd": coder_cmd,
        "ollama_model": ollama_model,
        "policy_valid": policy_valid,
        "evolution_status": evo_status,
        "candidates": candidates,
    }


def print_dashboard(summary: dict[str, object], output_func: Callable[[str], None] = print) -> None:
    """Print the interactive dashboard header and current state."""
    output_func("=" * 64)
    output_func(f"  APOS {__version__} - AI 프로젝트 운영체제 오케스트레이터")
    output_func("=" * 64)
    output_func("")
    output_func("[현재 상태]")
    output_func(f"  - 프로젝트 경로: {summary['root']}")
    status_text = "깨끗함" if summary["is_clean"] else "변경 사항 있음(Dirty)"
    output_func(f"  - Git 브랜치: {summary['branch']} ({status_text})")

    evo = summary.get("evolution_status")
    if isinstance(evo, dict):
        baseline = evo.get("baseline", {})
        if isinstance(baseline, dict):
            output_func(f"  - 진화 기준선: {baseline.get('ref', '-')} ({baseline.get('version', '-')})")
        output_func(f"  - 진화 상태: {format_status(evo.get('status'))}")
    else:
        output_func("  - 진화 상태: 비활성 또는 정책 없음")

    coder_info = summary.get("ollama_model")
    if coder_info:
        output_func(f"  - 로컬 코더: Ollama ({coder_info})")
    elif summary.get("coder_cmd"):
        output_func(f"  - 로컬 코더: {summary.get('coder_cmd')}")
    else:
        output_func("  - 로컬 코더: <설정되지 않음>")

    candidates = summary.get("candidates")
    if isinstance(candidates, list) and candidates:
        output_func(f"  - 활성 후보 ({len(candidates)}개):")
        for item in candidates[:3]:
            if isinstance(item, dict):
                output_func(
                    f"      * {item.get('candidate_id')} [{format_status(item.get('status'))}] "
                    f"-> 목표 {item.get('target_version')}"
                )
        if len(candidates) > 3:
            output_func(f"      ...외 {len(candidates) - 3}개")
    else:
        output_func("  - 활성 후보: 등록된 후보 없음")

    output_func("")


def run_orchestrator(
    root: Path | None = None,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
) -> int:
    """Main interactive loop for APOS orchestrator."""
    if root is None:
        root = GitClient(Path.cwd()).ensure_repo().resolve()
    else:
        root = GitClient(root).ensure_repo().resolve()

    while True:
        summary = get_project_summary(root)
        print_dashboard(summary, output_func=output_func)

        output_func("[다음으로 할 수 있는 일]")
        output_func("  1. 원스톱 자기 진화 가이드 (개선안 -> 후보 생성 -> 개발 -> 평가 -> 검수 안내)")
        output_func("  2. 새 진화 제안서(Proposal) 작성 및 후보 등록")
        output_func("  3. 기존 후보 개발 실행 (apos evolution run)")
        output_func("  4. 후보 상태 평가 (apos evolution evaluate)")
        output_func("  5. 검수 결과 기록 (Codex / Human 승인 또는 반려)")
        output_func("  6. 상태 및 정책 검증 조회")
        output_func("  0. 종료")
        output_func("")

        try:
            choice = input_func("원하는 작업 번호를 선택하세요 [0-6]: ").strip()
        except (EOFError, KeyboardInterrupt):
            output_func("\n오케스트레이터를 종료합니다.")
            return 0

        output_func("")
        if choice == "1":
            guide_evolution(root, input_func=input_func, output_func=output_func)
        elif choice == "2":
            interactive_create_proposal_and_candidate(root, input_func=input_func, output_func=output_func)
        elif choice == "3":
            interactive_run_candidate(root, input_func=input_func, output_func=output_func)
        elif choice == "4":
            interactive_evaluate_candidate(root, input_func=input_func, output_func=output_func)
        elif choice == "5":
            interactive_review_candidate(root, input_func=input_func, output_func=output_func)
        elif choice == "6":
            interactive_show_status(root, output_func=output_func)
        elif choice == "0" or choice.lower() in {"q", "quit", "exit"}:
            output_func("오케스트레이터를 종료합니다.")
            return 0
        else:
            output_func("잘못된 입력입니다. 0부터 6 사이의 번호를 입력해주세요.")

        output_func("\n" + "-" * 64 + "\n")


def guide_evolution(
    root: Path,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
) -> bool:
    """Step-by-step guided evolution workflow."""
    output_func("================================================================")
    output_func("  [1단계] 원스톱 자기 진화 가이드 (One-stop Guided Evolution)")
    output_func("================================================================")
    output_func("APOS가 다음 버전 개선을 위한 제안서 생성부터 검수 안내까지 순차적으로 진행합니다.\n")

    # Check clean workspace
    git = GitClient(root)
    if git.status_porcelain().strip():
        output_func("경고: Git 작업공간에 변경 사항(Dirty state)이 있습니다.")
        output_func("자기 진화 후보 생성을 위해서는 깨끗한 작업공간이 필요합니다.")
        output_func("작업을 커밋하거나 stash한 후 다시 시도해주세요.")
        return False

    # 1. Goal & Target Version
    output_func("1. 개선 방향 및 목표 정의")
    default_goal = "Improve APOS interactive orchestration and usability"
    try:
        goal = input_func(f"개선 목표를 입력하세요 [기본값: {default_goal}]: ").strip()
        if not goal:
            goal = default_goal

        default_target = "1.2.0"
        target_version = input_func(f"목표 버전 (SemVer) [기본값: {default_target}]: ").strip()
        if not target_version:
            target_version = default_target

        candidate_id = input_func(f"후보 ID (영문소문자, 숫자, 하이픈) [기본값: evo-{target_version.replace('.', '-') }]: ").strip()
        if not candidate_id:
            candidate_id = f"evo-{target_version.replace('.', '-')}"
    except (EOFError, KeyboardInterrupt):
        output_func("\n취소되었습니다.")
        return False

    # Load policy if available for default test commands
    test_commands = ["python -m unittest discover -s tests"]
    try:
        policy = EvolutionPolicy.load(root)
        if policy.required_test_commands:
            test_commands = list(policy.required_test_commands)
    except Exception:
        pass

    # Default allowed files
    default_allowed = [
        "pyproject.toml",
        "src/apos/__init__.py",
        "src/apos/cli.py",
        "src/apos/orchestrator.py",
        "src/apos/draft.py",
        "README.md",
        "SELF_EVOLUTION.md",
        "tests/test_cli_korean.py",
        "tests/test_orchestrator.py",
    ]
    # If app.py exists (e.g. in test fixture or specific app), include it if present
    if (root / "app.py").exists() and "app.py" not in default_allowed:
        default_allowed.append("app.py")

    # Proposal template
    proposal_data: dict[str, object] = {
        "proposal_id": f"PROP-{candidate_id.upper()}",
        "title": f"APOS {target_version} Evolution",
        "goal": goal,
        "target_version": target_version,
        "base_ref": "v1.1.0",
        "risk": "medium",
        "allowed_files": default_allowed,
        "read_only_files": [
            "src/apos/models.py",
            "src/apos/kernel.py",
            "src/apos/evolution.py",
        ],
        "constraints": [
            "Keep existing public CLI behavior backward compatible.",
            "Set the APOS package version to exactly the target version in pyproject.toml and src/apos/__init__.py.",
            "Add focused regression tests for every behavior change.",
            "Do not modify immutable benchmark controls or evolution policy.",
        ],
        "expected_behavior": [
            f"APOS runtime version reports {target_version}.",
            "All unit tests pass cleanly.",
        ],
        "test_commands": test_commands,
        "max_attempts": 3,
    }

    output_func("\n[생성된 진화 제안서 요약]")
    output_func(f"  - 제안 ID: {proposal_data['proposal_id']}")
    output_func(f"  - 목표 버전: {proposal_data['target_version']}")
    output_func(f"  - 목표: {proposal_data['goal']}")
    output_func(f"  - 기준선: {proposal_data['base_ref']}")
    output_func(f"  - 허용 파일 수: {len(proposal_data['allowed_files'])}개")

    proposal_dir = root / ".apos" / "evolution" / "proposals"
    proposal_dir.mkdir(parents=True, exist_ok=True)
    proposal_file = proposal_dir / f"{candidate_id}.json"
    proposal_file.write_text(json.dumps(proposal_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    output_func(f"  - 제안서 파일 저장됨: {proposal_file.relative_to(root)}")

    # 2. Confirm candidate creation
    try:
        proceed = input_func("\n격리된 후보(Candidate) 복제본을 생성하시겠습니까? (Y/n): ").strip().lower()
        if proceed in {"n", "no"}:
            output_func("후보 생성이 취소되었습니다.")
            return False
    except (EOFError, KeyboardInterrupt):
        output_func("\n취소되었습니다.")
        return False

    try:
        candidate_meta = create_candidate(root, proposal_file, candidate_id=candidate_id)
        output_func("\n[후보 생성 완료]")
        output_func(f"  - 후보 ID: {candidate_meta['candidate_id']}")
        output_func(f"  - 격리 브랜치: {candidate_meta['branch']}")
        output_func(f"  - 작업공간: {candidate_meta['workspace']}")
    except EvolutionError as exc:
        output_func(f"후보 생성 실패: {exc}")
        return False

    # 3. Confirm development run
    try:
        run_now = input_func("\n로컬 코더로 후보 개발을 바로 실행하시겠습니까? (Y/n): ").strip().lower()
        if run_now in {"n", "no"}:
            output_func(f"나중에 `apos evolution run {candidate_id}` 또는 메뉴 3번을 통해 실행할 수 있습니다.")
            return True
    except (EOFError, KeyboardInterrupt):
        output_func("\n개발 실행이 보류되었습니다.")
        return True

    output_func(f"\n[후보 개발 실행 중...] candidate_id={candidate_id}")
    coder_cmd = configured_coder_command(root)
    try:
        run_result = run_candidate(
            root,
            candidate_id,
            RunOptions(coder_command=coder_cmd, command_timeout_seconds=300),
        )
        run_data = run_result.get("run", {})
        status = run_data.get("status")
        output_func(f"  - 개발 결과: {format_status(status)}")
        output_func(f"  - 커밋: {run_data.get('commit_hash') or '없음'}")
        if status != "PASS":
            output_func("개발이 통과하지 못했습니다. 로그를 확인하고 다시 실행하세요.")
            return False
    except EvolutionError as exc:
        output_func(f"후보 개발 실행 실패: {exc}")
        return False

    # 4. Evaluation
    try:
        eval_now = input_func("\n신뢰된 기준선(단위테스트 + 벤치마크)으로 후보를 평가하시겠습니까? (Y/n): ").strip().lower()
        if eval_now in {"n", "no"}:
            output_func(f"나중에 `apos evolution evaluate {candidate_id}` 또는 메뉴 4번으로 평가할 수 있습니다.")
            return True
    except (EOFError, KeyboardInterrupt):
        output_func("\n평가가 보류되었습니다.")
        return True

    output_func(f"\n[후보 기준선 평가 실행 중...] candidate_id={candidate_id}")
    try:
        eval_report = evaluate_candidate(root, candidate_id, quick=False, timeout_seconds=600)
        report_status = eval_report.get("status")
        output_func(f"  - 평가 결과: {format_status(report_status)}")
        output_func(f"  - 검수 문서: {eval_report.get('review_path')}")
        if report_status != "READY_FOR_REVIEW":
            output_func("평가가 통과하지 못했습니다. 평가 게이트를 확인하세요.")
            return False
    except EvolutionError as exc:
        output_func(f"후보 평가 실패: {exc}")
        return False

    # 5. Reviews
    output_func("\n================================================================")
    output_func("  [검수 단계] 필수 검수자(Codex 및 Human)의 검수를 기록합니다.")
    output_func("================================================================")

    # Codex review
    try:
        codex_approve = input_func("Codex 검수를 승인으로 기록하시겠습니까? (Y/n): ").strip().lower()
        if codex_approve not in {"n", "no"}:
            note = input_func("Codex 검수 근거 [기본값: AI code review and benchmark verification passed]: ").strip()
            if not note:
                note = "AI code review and benchmark verification passed."
            record_review(root, candidate_id, reviewer="codex", decision="approve", note=note)
            output_func("  + Codex 검수 승인 기록 완료.")
    except Exception as exc:
        output_func(f"Codex 검수 기록 중 오류: {exc}")

    # Human review
    try:
        human_approve = input_func("사용자(Human) 최종 승인을 기록하시겠습니까? (Y/n): ").strip().lower()
        if human_approve not in {"n", "no"}:
            note = input_func("사용자 승인 메모 [기본값: Verified CUI orchestrator functionality]: ").strip()
            if not note:
                note = "Verified CUI orchestrator functionality."
            res = record_review(root, candidate_id, reviewer="human", decision="approve", note=note)
            output_func("  + 사용자(Human) 승인 기록 완료.")

            promotion = res.get("promotion", {})
            if promotion.get("status") == "PROMOTABLE":
                output_func("\n" + "*" * 64)
                output_func("  축하합니다! 후보가 승격 가능(PROMOTABLE) 상태가 되었습니다.")
                output_func("*" * 64)
                output_func("\n[중요 안전 안내]")
                output_func("APOS 안전 거버넌스 규칙에 따라 시스템은 자동 머지 및 태그를 수행하지 않습니다.")
                output_func("릴리스를 완성하려면 외부 Git 터미널에서 다음 명령을 직접 실행하세요:\n")
                branch = candidate_meta.get("branch")
                target_v = proposal_data.get("target_version")
                output_func(f"    git checkout master")
                output_func(f"    git merge --no-ff {branch} -m \"Release v{target_v}\"")
                output_func(f"    git tag v{target_v}")
                output_func(f"    pip install -e .\n")
    except Exception as exc:
        output_func(f"사용자 승인 기록 중 오류: {exc}")

    return True


def interactive_create_proposal_and_candidate(
    root: Path,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
) -> None:
    """Create a proposal and register an isolated candidate."""
    output_func("[새 진화 제안서 작성 및 후보 등록]")
    try:
        goal = input_func("개선 목표 설명: ").strip()
        if not goal:
            output_func("개선 목표는 필수입니다.")
            return

        target_version = input_func("목표 버전 (예: 1.2.0): ").strip()
        if not target_version:
            output_func("목표 버전은 필수입니다.")
            return

        candidate_id = input_func(f"후보 ID [기본값: evo-{target_version.replace('.', '-') }]: ").strip()
        if not candidate_id:
            candidate_id = f"evo-{target_version.replace('.', '-')}"

        allowed_files_input = input_func("수정 허용 파일 (쉼표 구분) [기본값: pyproject.toml, src/apos/__init__.py, src/apos/cli.py, src/apos/orchestrator.py]: ").strip()
        if allowed_files_input:
            allowed_files = [f.strip() for f in allowed_files_input.split(",") if f.strip()]
        else:
            allowed_files = ["pyproject.toml", "src/apos/__init__.py", "src/apos/cli.py", "src/apos/orchestrator.py"]
            if (root / "app.py").exists():
                allowed_files.append("app.py")

        test_commands = ["python -m unittest discover -s tests"]
        try:
            policy = EvolutionPolicy.load(root)
            if policy.required_test_commands:
                test_commands = list(policy.required_test_commands)
        except Exception:
            pass

        proposal_data = {
            "proposal_id": f"PROP-{candidate_id.upper()}",
            "title": f"Evolution to {target_version}",
            "goal": goal,
            "target_version": target_version,
            "base_ref": "v1.1.0",
            "risk": "medium",
            "allowed_files": allowed_files,
            "read_only_files": ["src/apos/models.py", "src/apos/kernel.py"],
            "constraints": [
                "Keep existing public CLI behavior backward compatible.",
                f"Set version to {target_version}.",
            ],
            "expected_behavior": [
                f"Runtime version reports {target_version}.",
                "All unit tests pass.",
            ],
            "test_commands": test_commands,
            "max_attempts": 3,
        }

        proposal_dir = root / ".apos" / "evolution" / "proposals"
        proposal_dir.mkdir(parents=True, exist_ok=True)
        proposal_file = proposal_dir / f"{candidate_id}.json"
        proposal_file.write_text(json.dumps(proposal_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        output_func(f"제안서 파일 생성 완료: {proposal_file.relative_to(root)}")

        candidate = create_candidate(root, proposal_file, candidate_id=candidate_id)
        output_func(f"진화 후보 등록 완료: {candidate['candidate_id']}")
        output_func(f"  - 브랜치: {candidate['branch']}")
        output_func(f"  - 작업공간: {candidate['workspace']}")
    except Exception as exc:
        output_func(f"오류: {exc}")


def interactive_select_candidate(
    root: Path,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
) -> str | None:
    """Helper to let the user select a candidate from list."""
    candidates = list_candidates(root)
    if not candidates:
        output_func("등록된 진화 후보가 없습니다. 먼저 후보를 생성해주세요.")
        return None

    output_func("후보 목록:")
    for idx, c in enumerate(candidates, start=1):
        output_func(f"  {idx}. {c.get('candidate_id')} [{format_status(c.get('status'))}] (목표: {c.get('target_version')})")

    try:
        choice = input_func(f"후보 번호를 선택하세요 [1-{len(candidates)}]: ").strip()
        idx = int(choice) - 1
        if 0 <= idx < len(candidates):
            return str(candidates[idx].get("candidate_id"))
        output_func("잘못된 번호입니다.")
        return None
    except Exception:
        output_func("취소되었거나 유효하지 않은 입력입니다.")
        return None


def interactive_run_candidate(
    root: Path,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
) -> None:
    """Select a candidate and run local development."""
    cid = interactive_select_candidate(root, input_func, output_func)
    if not cid:
        return

    output_func(f"\n후보 '{cid}' 개발 실행 중...")
    coder_cmd = configured_coder_command(root)
    try:
        res = run_candidate(root, cid, RunOptions(coder_command=coder_cmd, command_timeout_seconds=300))
        run = res.get("run", {})
        output_func(f"실행 결과: {format_status(run.get('status'))}")
        output_func(f"커밋: {run.get('commit_hash') or '생략됨'}")
    except Exception as exc:
        output_func(f"실행 오류: {exc}")


def interactive_evaluate_candidate(
    root: Path,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
) -> None:
    """Select a candidate and evaluate against baseline."""
    cid = interactive_select_candidate(root, input_func, output_func)
    if not cid:
        return

    try:
        quick_in = input_func("빠른 검증(단위테스트만)으로 실행할까요? (y/N) [기본값: 전체 평가]: ").strip().lower()
        quick = quick_in in {"y", "yes"}
        output_func(f"\n후보 '{cid}' 평가 실행 중... (quick={quick})")
        report = evaluate_candidate(root, cid, quick=quick, timeout_seconds=600)
        output_func(f"평가 결과: {format_status(report.get('status'))}")
        gates = report.get("gates", [])
        if isinstance(gates, list):
            for gate in gates:
                if isinstance(gate, dict):
                    output_func(f"  * {format_status(gate.get('status'))} {gate.get('name')}: {gate.get('detail')}")
        output_func(f"검수 자료: {report.get('review_path')}")
    except Exception as exc:
        output_func(f"평가 오류: {exc}")


def interactive_review_candidate(
    root: Path,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
) -> None:
    """Select a candidate and record review decision."""
    cid = interactive_select_candidate(root, input_func, output_func)
    if not cid:
        return

    try:
        rev = input_func("검수자 (1: codex, 2: human) [1/2]: ").strip()
        reviewer = "codex" if rev == "1" else "human" if rev == "2" else rev.lower()

        dec = input_func("결정 (1: approve [승인], 2: reject [반려]) [1/2]: ").strip()
        decision = "approve" if dec == "1" else "reject" if dec == "2" else dec.lower()

        note = input_func("검수 사유/근거: ").strip()
        if not note:
            note = f"{reviewer} reviewed and {decision}ed the candidate."

        res = record_review(root, cid, reviewer=reviewer, decision=decision, note=note)
        promotion = res.get("promotion", {})
        output_func(f"검수 기록 완료! 현재 승격 상태: {format_status(promotion.get('status'))}")
        if promotion.get("missing_reviewers"):
            output_func(f"남은 검수자: {', '.join(promotion.get('missing_reviewers', []))}")
    except Exception as exc:
        output_func(f"검수 기록 오류: {exc}")


def interactive_show_status(
    root: Path,
    output_func: Callable[[str], None] = print,
) -> None:
    """Show detailed evolution and governance status."""
    try:
        status = evolution_status(root)
        baseline = status.get("baseline", {}) if isinstance(status.get("baseline"), dict) else {}
        gov = status.get("governance", {}) if isinstance(status.get("governance"), dict) else {}
        output_func("[상세 진화 및 거버넌스 상태]")
        output_func(f"  - 진화 상태: {format_status(status.get('status'))}")
        output_func(f"  - 기준선: {baseline.get('ref')} (v{baseline.get('version')}) [{baseline.get('commit')}]")
        output_func(f"  - 정책 해시: {status.get('policy_hash')}")
        output_func(f"  - 필수 검수자: {', '.join(gov.get('required_reviewers', []))}")
        output_func("  - 자동 승격: 비활성화 (외부 수동 승격만 가능)")

        candidates = list_candidates(root)
        output_func(f"\n[후보 목록 ({len(candidates)}개)]")
        for c in candidates:
            output_func(f"  - {c.get('candidate_id')} [{format_status(c.get('status'))}] 목표: {c.get('target_version')}")
    except Exception as exc:
        output_func(f"상태 조회 오류: {exc}")
