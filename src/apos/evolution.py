from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any
from uuid import uuid4

from .benchmark import validate_benchmark_suite
from .config import apos_dir
from .git import GitClient, GitError
from .kernel import Kernel, RunOptions
from .models import TaskSpec
from .pathing import PathPolicyError, normalize_project_path, project_path


POLICY_PATH = ".apos/evolution-policy.json"
RUNTIME_PATH = ".apos/evolution"
_VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class EvolutionError(ValueError):
    """Raised when the APOS evolution contract cannot be satisfied."""


@dataclass(frozen=True)
class EvolutionPolicy:
    schema_version: int
    baseline_version: str
    baseline_ref: str
    maximum_version_exclusive: str
    branch_prefix: str
    required_test_commands: list[str]
    benchmark_command: str
    benchmark_suite: str
    benchmark_trusted_replay: bool
    benchmark_minimum_pass_rate: float
    benchmark_minimum_quality_score: float
    immutable_paths: list[str]
    required_reviewers: list[str]
    auto_promotion: bool
    baseline_evidence: dict[str, object] = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path) -> "EvolutionPolicy":
        path = project_path(root, POLICY_PATH)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise EvolutionError(f"evolution policy not found: {POLICY_PATH}") from exc
        except json.JSONDecodeError as exc:
            raise EvolutionError(f"invalid JSON in {POLICY_PATH}: {exc}") from exc
        if not isinstance(data, dict):
            raise EvolutionError("evolution policy must be a JSON object")
        return cls.from_mapping(data)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "EvolutionPolicy":
        schema_version = data.get("schema_version")
        if schema_version != 1:
            raise EvolutionError("evolution policy schema_version must be 1")
        baseline = _mapping(data, "baseline")
        candidates = _mapping(data, "candidates")
        verification = _mapping(data, "verification")
        governance = _mapping(data, "governance")
        benchmark = _mapping(verification, "benchmark")

        baseline_version = _required_string(baseline, "version")
        maximum_version = _required_string(candidates, "maximum_version_exclusive")
        _parse_version(baseline_version)
        if _parse_version(maximum_version) <= _parse_version(baseline_version):
            raise EvolutionError("maximum candidate version must be greater than the baseline version")

        required_reviewers = [item.lower() for item in _string_list(governance, "required_reviewers")]
        if not required_reviewers or len(required_reviewers) != len(set(required_reviewers)):
            raise EvolutionError("required_reviewers must contain unique reviewer names")
        auto_promotion = governance.get("auto_promotion")
        if auto_promotion is not False:
            raise EvolutionError("auto_promotion must be false for APOS 1.x")

        minimum_pass_rate = benchmark.get("minimum_pass_rate")
        minimum_quality_score = benchmark.get("minimum_quality_score")
        if not isinstance(minimum_pass_rate, int | float) or not 0 <= minimum_pass_rate <= 1:
            raise EvolutionError("benchmark minimum_pass_rate must be between 0 and 1")
        if not isinstance(minimum_quality_score, int | float) or not 0 <= minimum_quality_score <= 100:
            raise EvolutionError("benchmark minimum_quality_score must be between 0 and 100")
        if benchmark.get("trusted_replay") is not True:
            raise EvolutionError("benchmark trusted_replay must be true for APOS 1.x")

        immutable_paths = [normalize_project_path(path) for path in _string_list(governance, "immutable_paths")]
        if POLICY_PATH not in immutable_paths:
            raise EvolutionError(f"immutable_paths must include {POLICY_PATH}")
        required_tests = _string_list(verification, "required_test_commands")
        if not required_tests:
            raise EvolutionError("required_test_commands must contain at least one command")

        evidence = baseline.get("evidence", {})
        if not isinstance(evidence, dict):
            raise EvolutionError("baseline evidence must be an object")
        return cls(
            schema_version=1,
            baseline_version=baseline_version,
            baseline_ref=_required_string(baseline, "ref"),
            maximum_version_exclusive=maximum_version,
            branch_prefix=_required_string(candidates, "branch_prefix"),
            required_test_commands=required_tests,
            benchmark_command=_required_string(benchmark, "command"),
            benchmark_suite=normalize_project_path(_required_string(benchmark, "suite")),
            benchmark_trusted_replay=True,
            benchmark_minimum_pass_rate=float(minimum_pass_rate),
            benchmark_minimum_quality_score=float(minimum_quality_score),
            immutable_paths=immutable_paths,
            required_reviewers=required_reviewers,
            auto_promotion=False,
            baseline_evidence=dict(evidence),
        )


@dataclass(frozen=True)
class EvolutionProposal:
    proposal_id: str
    title: str
    goal: str
    target_version: str
    base_ref: str | None
    risk: str
    allowed_files: list[str]
    read_only_files: list[str]
    constraints: list[str]
    expected_behavior: list[str]
    test_commands: list[str]
    max_attempts: int

    @classmethod
    def load(cls, path: Path, policy: EvolutionPolicy) -> "EvolutionProposal":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise EvolutionError(f"evolution proposal not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise EvolutionError(f"invalid proposal JSON in {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise EvolutionError("evolution proposal must be a JSON object")
        return cls.from_mapping(data, policy)

    @classmethod
    def from_mapping(cls, data: dict[str, Any], policy: EvolutionPolicy) -> "EvolutionProposal":
        target_version = _required_string(data, "target_version")
        target = _parse_version(target_version)
        if target <= _parse_version(policy.baseline_version):
            raise EvolutionError("target_version must be greater than the 1.1 baseline")
        if target >= _parse_version(policy.maximum_version_exclusive):
            raise EvolutionError(f"target_version must be lower than {policy.maximum_version_exclusive}")

        risk = str(data.get("risk", "medium")).lower()
        if risk not in {"low", "medium", "high"}:
            raise EvolutionError("risk must be low, medium, or high")
        max_attempts = data.get("max_attempts", 3)
        if not isinstance(max_attempts, int) or max_attempts < 1:
            raise EvolutionError("max_attempts must be a positive integer")
        allowed_files = [normalize_project_path(path) for path in _string_list(data, "allowed_files")]
        if not allowed_files:
            raise EvolutionError("allowed_files must contain at least one path")
        immutable = sorted(set(allowed_files).intersection(policy.immutable_paths))
        if immutable:
            raise EvolutionError(f"proposal may not write immutable path(s): {', '.join(immutable)}")
        test_commands = _string_list(data, "test_commands")
        if not test_commands:
            raise EvolutionError("test_commands must contain at least one command")
        base_ref = data.get("base_ref")
        if base_ref is not None and (not isinstance(base_ref, str) or not base_ref.strip()):
            raise EvolutionError("base_ref must be a non-empty string")
        return cls(
            proposal_id=_required_string(data, "proposal_id"),
            title=_required_string(data, "title"),
            goal=_required_string(data, "goal"),
            target_version=target_version,
            base_ref=base_ref.strip() if isinstance(base_ref, str) else None,
            risk=risk,
            allowed_files=allowed_files,
            read_only_files=[normalize_project_path(path) for path in _string_list(data, "read_only_files")],
            constraints=_string_list(data, "constraints"),
            expected_behavior=_string_list(data, "expected_behavior"),
            test_commands=test_commands,
            max_attempts=max_attempts,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "proposal_id": self.proposal_id,
            "title": self.title,
            "goal": self.goal,
            "target_version": self.target_version,
            "base_ref": self.base_ref,
            "risk": self.risk,
            "allowed_files": list(self.allowed_files),
            "read_only_files": list(self.read_only_files),
            "constraints": list(self.constraints),
            "expected_behavior": list(self.expected_behavior),
            "test_commands": list(self.test_commands),
            "max_attempts": self.max_attempts,
        }

    def to_task_spec(self, candidate_id: str, branch: str, policy: EvolutionPolicy) -> TaskSpec:
        constraints = [
            *self.constraints,
            f"Set the APOS package version to exactly {self.target_version} in every version source.",
            "Preserve the APOS 1.1 evolution policy and all immutable benchmark controls.",
            "Do not promote, merge, or tag the candidate.",
        ]
        return TaskSpec.from_mapping(
            {
                "task_id": f"EVOLVE-{candidate_id.upper()}",
                "title": self.title,
                "goal": self.goal,
                "branch": branch,
                "allowed_files": self.allowed_files,
                "read_only_files": sorted(set([*self.read_only_files, *policy.immutable_paths])),
                "constraints": constraints,
                "expected_behavior": [*self.expected_behavior, f"Runtime version reports {self.target_version}."],
                "test_commands": self.test_commands,
                "max_attempts": self.max_attempts,
            }
        )


def validate_evolution(root: Path) -> dict[str, object]:
    root = GitClient(root).ensure_repo().resolve()
    policy = EvolutionPolicy.load(root)
    git = GitClient(root)
    baseline_commit = _resolve_commit(git, policy.baseline_ref)
    baseline_version = _version_at_ref(git, policy.baseline_ref)
    if baseline_version != policy.baseline_version:
        raise EvolutionError(
            f"baseline ref reports version {baseline_version}, expected {policy.baseline_version}"
        )
    current_policy_hash = _policy_hash(root)
    baseline_policy_hash = _policy_hash_at_ref(git, policy.baseline_ref)
    if current_policy_hash != baseline_policy_hash:
        raise EvolutionError("tracked evolution policy differs from the v1.1.0 policy")
    head_commit = _resolve_commit(git, "HEAD")
    if _is_ancestor(git, baseline_commit, head_commit):
        immutable_changes = sorted(
            set(_changed_files(git, baseline_commit, head_commit)).intersection(policy.immutable_paths)
        )
        if immutable_changes:
            raise EvolutionError(f"immutable 1.1 control(s) changed: {', '.join(immutable_changes)}")
    validate_benchmark_suite(root, project_path(root, policy.benchmark_suite))
    return {
        "status": "VALID",
        "policy_path": POLICY_PATH,
        "policy_hash": current_policy_hash,
        "baseline_policy_hash": baseline_policy_hash,
        "baseline_version": policy.baseline_version,
        "baseline_ref": policy.baseline_ref,
        "baseline_commit": baseline_commit,
        "maximum_version_exclusive": policy.maximum_version_exclusive,
        "required_reviewers": list(policy.required_reviewers),
        "auto_promotion": policy.auto_promotion,
    }


def evolution_status(root: Path, candidate_id: str | None = None) -> dict[str, object]:
    root = GitClient(root).ensure_repo().resolve()
    policy = EvolutionPolicy.load(root)
    git = GitClient(root)
    baseline_commit = _resolve_commit(git, policy.baseline_ref)
    baseline_policy_hash = _policy_hash_at_ref(git, policy.baseline_ref)
    head_commit = _resolve_commit(git, "HEAD")
    immutable_changes = sorted(set(_changed_files(git, baseline_commit, head_commit)).intersection(policy.immutable_paths))
    status: dict[str, object] = {
        "status": "ACTIVE" if _is_ancestor(git, baseline_commit, head_commit) else "OUTSIDE_BASELINE_LINEAGE",
        "policy_path": POLICY_PATH,
        "policy_hash": _policy_hash(root),
        "policy_pinned": _policy_hash(root) == baseline_policy_hash,
        "controls_pinned": not immutable_changes,
        "baseline": {
            "version": policy.baseline_version,
            "ref": policy.baseline_ref,
            "commit": baseline_commit,
            "evidence": policy.baseline_evidence,
        },
        "current": {"branch": git.current_branch(), "commit": head_commit, "version": _read_workspace_version(root)},
        "governance": {
            "required_reviewers": list(policy.required_reviewers),
            "auto_promotion": False,
            "promotion_mode": "external_manual_action_only",
        },
    }
    if candidate_id:
        metadata = load_candidate(root, candidate_id)
        status["candidate"] = metadata
        status["promotion"] = candidate_promotion_status(root, candidate_id)
    else:
        status["candidates"] = list_candidates(root)
    return status


def create_candidate(
    root: Path,
    proposal_path: Path,
    candidate_id: str | None = None,
    base_ref: str | None = None,
) -> dict[str, object]:
    root = GitClient(root).ensure_repo().resolve()
    git = GitClient(root)
    policy = EvolutionPolicy.load(root)
    validate_evolution(root)
    proposal = EvolutionProposal.load(proposal_path, policy)
    if git.status_porcelain().strip():
        raise EvolutionError("trusted workspace must be clean before creating an evolution candidate")

    selected_base = base_ref or proposal.base_ref or policy.baseline_ref
    baseline_commit = _resolve_commit(git, policy.baseline_ref)
    parent_commit = _resolve_commit(git, selected_base)
    if not _is_ancestor(git, baseline_commit, parent_commit):
        raise EvolutionError(f"candidate base {selected_base} is not descended from {policy.baseline_ref}")
    parent_version = _version_at_ref(git, selected_base)
    if _parse_version(proposal.target_version) <= _parse_version(parent_version):
        raise EvolutionError(
            f"target_version {proposal.target_version} must be greater than parent version {parent_version}"
        )

    generated_id = f"{_slug(proposal.proposal_id)}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    normalized_id = _candidate_id(candidate_id or generated_id)
    artifact_dir = _candidate_artifact_dir(root, normalized_id)
    metadata_path = artifact_dir / "candidate.json"
    workspace = artifact_dir / "workspace"
    if artifact_dir.exists() or _branch_exists(git, f"{policy.branch_prefix}{normalized_id}"):
        raise EvolutionError(f"candidate already exists: {normalized_id}")

    branch = f"{policy.branch_prefix}{normalized_id}"
    git.exclude_path(f"{RUNTIME_PATH}/")
    workspace.parent.mkdir(parents=True, exist_ok=False)
    try:
        git.run(["worktree", "add", "-b", branch, str(workspace), parent_commit])
    except GitError as exc:
        raise EvolutionError(str(exc)) from exc

    metadata: dict[str, object] = {
        "schema_version": 1,
        "candidate_id": normalized_id,
        "status": "CREATED",
        "created_at": _now(),
        "workspace": workspace.relative_to(root).as_posix(),
        "branch": branch,
        "baseline_ref": policy.baseline_ref,
        "baseline_commit": baseline_commit,
        "parent_ref": selected_base,
        "parent_commit": parent_commit,
        "parent_version": parent_version,
        "target_version": proposal.target_version,
        "policy_hash": _policy_hash(root),
        "proposal": proposal.to_dict(),
        "development_runs": [],
    }
    _write_json(metadata_path, metadata)
    return metadata


def run_candidate(
    root: Path,
    candidate_id: str,
    options: RunOptions,
) -> dict[str, object]:
    root = GitClient(root).ensure_repo().resolve()
    policy = EvolutionPolicy.load(root)
    validate_evolution(root)
    metadata = load_candidate(root, candidate_id)
    _require_policy_match(root, metadata)
    workspace = _candidate_workspace(root, metadata)
    candidate_git = GitClient(workspace)
    if candidate_git.current_branch() != metadata.get("branch"):
        raise EvolutionError("candidate worktree is not on its registered evolution branch")
    proposal_data = metadata.get("proposal")
    if not isinstance(proposal_data, dict):
        raise EvolutionError("candidate proposal snapshot is missing")
    proposal = EvolutionProposal.from_mapping(proposal_data, policy)
    spec = proposal.to_task_spec(str(metadata["candidate_id"]), str(metadata["branch"]), policy)
    summary = Kernel(workspace).run_task(spec, options)

    runs = metadata.get("development_runs")
    if not isinstance(runs, list):
        runs = []
    runs.append({"recorded_at": _now(), **summary.to_dict()})
    metadata["development_runs"] = runs
    metadata["status"] = "DEVELOPED" if summary.status == "PASS" else "DEVELOPMENT_FAILED"
    _save_candidate(root, metadata)
    return {"candidate": metadata, "run": summary.to_dict()}


def evaluate_candidate(
    root: Path,
    candidate_id: str,
    *,
    quick: bool = False,
    timeout_seconds: int = 600,
) -> dict[str, object]:
    root = GitClient(root).ensure_repo().resolve()
    trusted_git = GitClient(root)
    policy = EvolutionPolicy.load(root)
    metadata = load_candidate(root, candidate_id)
    workspace = _candidate_workspace(root, metadata)
    candidate_git = GitClient(workspace)
    candidate_commit = _resolve_commit(candidate_git, "HEAD")
    baseline_commit = _resolve_commit(trusted_git, policy.baseline_ref)
    parent_commit = str(metadata.get("parent_commit") or "")
    gates: list[dict[str, object]] = []

    _gate(gates, "trusted_workspace_clean", not trusted_git.status_porcelain().strip(), "trusted evaluator worktree is clean")
    policy_hash = _policy_hash(root)
    baseline_policy_hash = _policy_hash_at_ref(trusted_git, policy.baseline_ref)
    _gate(
        gates,
        "policy_pinned",
        policy_hash == baseline_policy_hash,
        "trusted policy matches the v1.1.0 policy",
    )
    _gate(gates, "policy_unchanged", metadata.get("policy_hash") == policy_hash, "candidate policy hash matches the trusted policy")
    _gate(gates, "candidate_workspace_clean", not candidate_git.status_porcelain().strip(), "candidate worktree is clean")
    _gate(gates, "candidate_branch", candidate_git.current_branch() == metadata.get("branch"), f"branch={candidate_git.current_branch()}")
    _gate(gates, "baseline_lineage", _is_ancestor(trusted_git, baseline_commit, candidate_commit), f"baseline={baseline_commit}")
    _gate(gates, "parent_lineage", bool(parent_commit) and _is_ancestor(trusted_git, parent_commit, candidate_commit), f"parent={parent_commit}")

    changed_from_parent = _changed_files(candidate_git, parent_commit, candidate_commit) if parent_commit else []
    changed_from_baseline = _changed_files(candidate_git, baseline_commit, candidate_commit)
    _gate(gates, "candidate_has_changes", bool(changed_from_parent), f"changed_files={len(changed_from_parent)}")
    immutable_changes = sorted(set(changed_from_baseline).intersection(policy.immutable_paths))
    _gate(
        gates,
        "immutable_controls",
        not immutable_changes,
        "no immutable controls changed" if not immutable_changes else f"changed={','.join(immutable_changes)}",
    )

    version_error = ""
    candidate_version: str | None = None
    try:
        candidate_version = _read_workspace_version(workspace)
        target_version = str(metadata.get("target_version") or "")
        parent_version = str(metadata.get("parent_version") or policy.baseline_version)
        version_ok = (
            candidate_version == target_version
            and _parse_version(candidate_version) > _parse_version(parent_version)
            and _parse_version(candidate_version) < _parse_version(policy.maximum_version_exclusive)
        )
        version_error = f"candidate={candidate_version}, target={target_version}, parent={parent_version}"
    except EvolutionError as exc:
        version_ok = False
        version_error = str(exc)
    _gate(gates, "version_contract", version_ok, version_error)

    environment = _candidate_environment(workspace)
    tests_passed = True
    for index, command in enumerate(policy.required_test_commands, start=1):
        result = _execute(command, workspace, timeout_seconds, environment)
        tests_passed = tests_passed and result["status"] == "PASS"
        _gate(
            gates,
            f"required_test_{index}",
            result["status"] == "PASS",
            f"{command} -> {result['status']}",
            result,
        )
        if result["status"] != "PASS":
            break

    benchmark_summary: dict[str, object] | None = None
    if quick:
        gates.append({"name": "benchmark", "status": "NOT_RUN", "detail": "quick evaluation requested"})
    elif not tests_passed:
        gates.append({"name": "benchmark", "status": "BLOCKED", "detail": "required tests failed"})
    else:
        benchmark_result = _execute(
            policy.benchmark_command,
            workspace,
            timeout_seconds,
            environment,
            output_limit=None,
        )
        if benchmark_result["status"] == "PASS":
            try:
                benchmark_payload = _extract_json_object(str(benchmark_result.get("stdout") or ""))
                benchmark_summary = _benchmark_evidence(benchmark_payload)
                replay = _trusted_benchmark_replay(
                    root,
                    candidate_commit,
                    benchmark_payload,
                    policy,
                    timeout_seconds,
                )
                benchmark_summary["trusted_replay"] = replay
                benchmark_ok = (
                    benchmark_summary["status"] == "PASS"
                    and float(benchmark_summary["pass_rate"]) >= policy.benchmark_minimum_pass_rate
                    and float(benchmark_summary["average_quality_score"]) >= policy.benchmark_minimum_quality_score
                    and replay["status"] == "PASS"
                )
                detail = (
                    f"pass_rate={benchmark_summary['pass_rate']}, "
                    f"score={benchmark_summary['average_quality_score']}, replay={replay['status']}"
                )
            except (EvolutionError, TypeError, ValueError) as exc:
                benchmark_ok = False
                detail = f"invalid benchmark evidence: {exc}"
        else:
            benchmark_ok = False
            detail = f"benchmark command failed with exit code {benchmark_result['exit_code']}"
        _gate(gates, "benchmark", benchmark_ok, detail, _bounded_execution_evidence(benchmark_result))

    failed = any(gate.get("status") in {"FAIL", "BLOCKED"} for gate in gates)
    if failed:
        report_status = "REJECTED"
    elif quick:
        report_status = "INCOMPLETE"
    else:
        report_status = "READY_FOR_REVIEW"
    report: dict[str, object] = {
        "schema_version": 1,
        "report_id": f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}",
        "status": report_status,
        "evaluated_at": _now(),
        "candidate_id": metadata["candidate_id"],
        "candidate_commit": candidate_commit,
        "candidate_version": candidate_version,
        "baseline_ref": policy.baseline_ref,
        "baseline_commit": baseline_commit,
        "parent_commit": parent_commit,
        "policy_hash": _policy_hash(root),
        "quick": quick,
        "changed_files": changed_from_parent,
        "cumulative_changed_files": changed_from_baseline,
        "diff_stat": _diff_stat(candidate_git, parent_commit, candidate_commit),
        "gates": gates,
        "benchmark": benchmark_summary,
        "promotion": {
            "eligible_for_review": report_status == "READY_FOR_REVIEW",
            "automatic": False,
            "required_reviewers": list(policy.required_reviewers),
        },
    }
    report_path = _candidate_artifact_dir(root, str(metadata["candidate_id"])) / "evaluations" / f"{report['report_id']}.json"
    _write_json(report_path, report)
    markdown_path = report_path.with_suffix(".md")
    markdown_path.write_text(_review_markdown(report), encoding="utf-8")
    report_hash = _file_hash(report_path)
    metadata["status"] = report_status
    metadata["last_evaluation"] = {
        "path": report_path.relative_to(root).as_posix(),
        "review_path": markdown_path.relative_to(root).as_posix(),
        "report_hash": report_hash,
        "status": report_status,
        "candidate_commit": candidate_commit,
    }
    _save_candidate(root, metadata)
    report["report_path"] = report_path.relative_to(root).as_posix()
    report["review_path"] = markdown_path.relative_to(root).as_posix()
    report["report_hash"] = report_hash
    return report


def record_review(
    root: Path,
    candidate_id: str,
    reviewer: str,
    decision: str,
    note: str,
) -> dict[str, object]:
    root = GitClient(root).ensure_repo().resolve()
    policy = EvolutionPolicy.load(root)
    validate_evolution(root)
    metadata = load_candidate(root, candidate_id)
    reviewer = reviewer.strip().lower()
    decision = decision.strip().lower()
    if reviewer not in policy.required_reviewers:
        raise EvolutionError(f"reviewer must be one of: {', '.join(policy.required_reviewers)}")
    if decision not in {"approve", "reject"}:
        raise EvolutionError("review decision must be approve or reject")
    if not note.strip():
        raise EvolutionError("review note is required")
    evaluation = metadata.get("last_evaluation")
    if not isinstance(evaluation, dict) or evaluation.get("status") != "READY_FOR_REVIEW":
        raise EvolutionError("candidate needs a full passing evaluation before review")
    workspace = _candidate_workspace(root, metadata)
    candidate_commit = _resolve_commit(GitClient(workspace), "HEAD")
    if candidate_commit != evaluation.get("candidate_commit"):
        raise EvolutionError("candidate changed after evaluation; run evaluation again")
    report_path = project_path(root, str(evaluation.get("path") or ""))
    if _file_hash(report_path) != evaluation.get("report_hash"):
        raise EvolutionError("evaluation report hash mismatch")

    review = {
        "schema_version": 1,
        "review_id": f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}",
        "candidate_id": metadata["candidate_id"],
        "candidate_commit": candidate_commit,
        "evaluation_report": evaluation["path"],
        "evaluation_hash": evaluation["report_hash"],
        "reviewer": reviewer,
        "decision": decision,
        "note": note.strip(),
        "recorded_at": _now(),
    }
    review_path = _candidate_artifact_dir(root, str(metadata["candidate_id"])) / "reviews" / f"{review['review_id']}-{reviewer}.json"
    _write_json(review_path, review)
    promotion = candidate_promotion_status(root, candidate_id)
    metadata["status"] = str(promotion["status"])
    _save_candidate(root, metadata)
    return {"review": review, "review_path": review_path.relative_to(root).as_posix(), "promotion": promotion}


def candidate_promotion_status(root: Path, candidate_id: str) -> dict[str, object]:
    root = GitClient(root).ensure_repo().resolve()
    policy = EvolutionPolicy.load(root)
    metadata = load_candidate(root, candidate_id)
    evaluation = metadata.get("last_evaluation")
    if not isinstance(evaluation, dict) or evaluation.get("status") != "READY_FOR_REVIEW":
        return {
            "status": "NOT_READY",
            "required_reviewers": list(policy.required_reviewers),
            "approvals": [],
            "automatic_promotion": False,
        }
    workspace = _candidate_workspace(root, metadata)
    candidate_git = GitClient(workspace)
    current_commit = _resolve_commit(candidate_git, "HEAD")
    if (
        current_commit != evaluation.get("candidate_commit")
        or candidate_git.status_porcelain().strip()
        or candidate_git.current_branch() != metadata.get("branch")
    ):
        return {
            "status": "STALE_EVALUATION",
            "candidate_commit": current_commit,
            "evaluated_commit": evaluation.get("candidate_commit"),
            "required_reviewers": list(policy.required_reviewers),
            "approvals": [],
            "automatic_promotion": False,
            "next_action": "run a full evaluation for the current clean candidate commit",
        }
    reviews_dir = _candidate_artifact_dir(root, str(metadata["candidate_id"])) / "reviews"
    decisions: dict[str, str] = {}
    matching_reviews: list[dict[str, object]] = []
    if reviews_dir.exists():
        for path in reviews_dir.glob("*.json"):
            try:
                review = _read_json(path)
            except (OSError, json.JSONDecodeError, EvolutionError):
                continue
            if (
                review.get("candidate_commit") == evaluation.get("candidate_commit")
                and review.get("evaluation_hash") == evaluation.get("report_hash")
                and review.get("reviewer") in policy.required_reviewers
            ):
                matching_reviews.append(review)
    matching_reviews.sort(key=lambda review: (str(review.get("recorded_at") or ""), str(review.get("review_id") or "")))
    for review in matching_reviews:
        decisions[str(review["reviewer"])] = str(review.get("decision") or "")
    rejected = sorted(name for name, decision in decisions.items() if decision == "reject")
    approvals = sorted(name for name, decision in decisions.items() if decision == "approve")
    missing = [name for name in policy.required_reviewers if name not in approvals]
    if rejected:
        status = "REVIEW_REJECTED"
    elif not missing:
        status = "PROMOTABLE"
    else:
        status = "AWAITING_REVIEWS"
    return {
        "status": status,
        "candidate_commit": evaluation.get("candidate_commit"),
        "required_reviewers": list(policy.required_reviewers),
        "approvals": approvals,
        "missing_reviewers": missing,
        "rejected_by": rejected,
        "automatic_promotion": False,
        "next_action": "external human-controlled merge and tag" if status == "PROMOTABLE" else "complete required reviews",
    }


def list_candidates(root: Path) -> list[dict[str, object]]:
    candidates_root = apos_dir(root) / "evolution" / "candidates"
    if not candidates_root.exists():
        return []
    items: list[dict[str, object]] = []
    for path in candidates_root.glob("*/candidate.json"):
        try:
            data = _read_json(path)
        except (OSError, json.JSONDecodeError, EvolutionError):
            continue
        items.append(
            {
                "candidate_id": data.get("candidate_id"),
                "status": data.get("status"),
                "target_version": data.get("target_version"),
                "branch": data.get("branch"),
                "created_at": data.get("created_at"),
            }
        )
    return sorted(items, key=lambda item: str(item.get("created_at") or ""), reverse=True)


def load_candidate(root: Path, candidate_id: str) -> dict[str, object]:
    path = _candidate_artifact_dir(root, _candidate_id(candidate_id)) / "candidate.json"
    try:
        data = _read_json(path)
    except FileNotFoundError as exc:
        raise EvolutionError(f"evolution candidate not found: {candidate_id}") from exc
    if data.get("candidate_id") != _candidate_id(candidate_id):
        raise EvolutionError("candidate metadata id mismatch")
    return data


def _save_candidate(root: Path, metadata: dict[str, object]) -> None:
    _write_json(_candidate_artifact_dir(root, str(metadata["candidate_id"])) / "candidate.json", metadata)


def _candidate_workspace(root: Path, metadata: dict[str, object]) -> Path:
    relative = metadata.get("workspace")
    if not isinstance(relative, str) or not relative:
        raise EvolutionError("candidate workspace is missing")
    try:
        workspace = project_path(root, relative)
    except PathPolicyError as exc:
        raise EvolutionError(str(exc)) from exc
    if not workspace.exists():
        raise EvolutionError(f"candidate workspace does not exist: {relative}")
    return workspace


def _candidate_artifact_dir(root: Path, candidate_id: str) -> Path:
    return apos_dir(root) / "evolution" / "candidates" / candidate_id


def _candidate_id(value: str) -> str:
    normalized = _slug(value)
    if normalized != value.lower() or len(normalized) > 64:
        raise EvolutionError("candidate id must contain only lowercase letters, numbers, and hyphens")
    return normalized


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")[:64] or "candidate"


def _resolve_commit(git: GitClient, ref: str) -> str:
    completed = git.run(["rev-parse", "--verify", f"{ref}^{{commit}}"], check=False)
    if completed.returncode != 0:
        raise EvolutionError(f"Git ref does not resolve to a commit: {ref}")
    return completed.stdout.strip()


def _is_ancestor(git: GitClient, ancestor: str, descendant: str) -> bool:
    return git.run(["merge-base", "--is-ancestor", ancestor, descendant], check=False).returncode == 0


def _branch_exists(git: GitClient, branch: str) -> bool:
    return git.run(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], check=False).returncode == 0


def _changed_files(git: GitClient, base: str, head: str) -> list[str]:
    if not base:
        return []
    output = git.run(["diff", "--name-only", f"{base}..{head}"], check=False).stdout
    return sorted(normalize_project_path(line) for line in output.splitlines() if line.strip())


def _diff_stat(git: GitClient, base: str, head: str) -> str:
    if not base:
        return ""
    return git.run(["diff", "--stat", f"{base}..{head}"], check=False).stdout.strip()


def _version_at_ref(git: GitClient, ref: str) -> str:
    pyproject = git.run(["show", f"{ref}:pyproject.toml"], check=False)
    init_file = git.run(["show", f"{ref}:src/apos/__init__.py"], check=False)
    if pyproject.returncode != 0 or init_file.returncode != 0:
        raise EvolutionError(f"cannot read APOS version sources at {ref}")
    return _versions_from_text(pyproject.stdout, init_file.stdout)


def _read_workspace_version(root: Path) -> str:
    try:
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
        init_file = (root / "src" / "apos" / "__init__.py").read_text(encoding="utf-8")
    except OSError as exc:
        raise EvolutionError(f"cannot read APOS version sources: {exc}") from exc
    return _versions_from_text(pyproject, init_file)


def _versions_from_text(pyproject: str, init_file: str) -> str:
    project_match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    init_match = re.search(r'^__version__\s*=\s*"([^"]+)"', init_file, re.MULTILINE)
    if not project_match or not init_match:
        raise EvolutionError("both pyproject.toml and src/apos/__init__.py must declare a version")
    project_version = project_match.group(1)
    init_version = init_match.group(1)
    _parse_version(project_version)
    if project_version != init_version:
        raise EvolutionError(f"version sources disagree: pyproject={project_version}, package={init_version}")
    return project_version


def _parse_version(value: str) -> tuple[int, int, int]:
    match = _VERSION_PATTERN.fullmatch(value)
    if not match:
        raise EvolutionError(f"version must use MAJOR.MINOR.PATCH: {value}")
    return tuple(int(part) for part in match.groups())


def _policy_hash(root: Path) -> str:
    try:
        data = json.loads(project_path(root, POLICY_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvolutionError(f"cannot hash {POLICY_PATH}: {exc}") from exc
    if not isinstance(data, dict):
        raise EvolutionError("evolution policy must be a JSON object")
    return _json_hash(data)


def _policy_hash_at_ref(git: GitClient, ref: str) -> str:
    completed = git.run(["show", f"{ref}:{POLICY_PATH}"], check=False)
    if completed.returncode != 0:
        raise EvolutionError(f"cannot read the evolution policy at {ref}")
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise EvolutionError(f"invalid evolution policy at {ref}: {exc}") from exc
    if not isinstance(data, dict):
        raise EvolutionError(f"evolution policy at {ref} must be an object")
    return _json_hash(data)


def _json_hash(data: object) -> str:
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise EvolutionError(f"cannot hash {path}: {exc}") from exc


def _require_policy_match(root: Path, metadata: dict[str, object]) -> None:
    if metadata.get("policy_hash") != _policy_hash(root):
        raise EvolutionError("candidate was created under a different evolution policy")


def _candidate_environment(workspace: Path) -> dict[str, str]:
    environment = dict(os.environ)
    source = str(workspace / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = source if not existing else f"{source}{os.pathsep}{existing}"
    environment["APOS_EVOLUTION_CANDIDATE"] = str(workspace)
    return environment


def _execute(
    command: str,
    cwd: Path,
    timeout_seconds: int,
    environment: dict[str, str],
    output_limit: int | None = 6000,
) -> dict[str, object]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            env=environment,
        )
        status = "PASS" if completed.returncode == 0 else "FAIL"
        return {
            "command": command,
            "status": status,
            "exit_code": completed.returncode,
            "stdout": _limit_output(completed.stdout, output_limit),
            "stderr": _limit_output(completed.stderr, output_limit),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "status": "FAIL",
            "exit_code": 124,
            "stdout": _limit_output(exc.stdout or "", output_limit),
            "stderr": _limit_output(exc.stderr or "", output_limit),
            "error": f"timed out after {timeout_seconds}s",
        }


def _extract_json_object(value: str) -> dict[str, object]:
    try:
        payload = json.loads(value)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, character in enumerate(value):
        if character != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(value[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise EvolutionError("benchmark command did not emit a JSON object")


def _bounded_execution_evidence(result: dict[str, object]) -> dict[str, object]:
    evidence = dict(result)
    evidence["stdout"] = _tail(str(result.get("stdout") or ""))
    evidence["stderr"] = _tail(str(result.get("stderr") or ""))
    return evidence


def _trusted_benchmark_replay(
    root: Path,
    candidate_commit: str,
    payload: dict[str, object],
    policy: EvolutionPolicy,
    timeout_seconds: int,
) -> dict[str, object]:
    suite = validate_benchmark_suite(root, project_path(root, policy.benchmark_suite))
    result_tasks = payload.get("tasks")
    if not isinstance(result_tasks, list) or len(result_tasks) != len(suite.tasks):
        raise EvolutionError("benchmark result task count does not match the trusted suite")
    replay_results: list[dict[str, object]] = []
    trusted_git = GitClient(root)
    for task_ref, item in zip(suite.tasks, result_tasks, strict=True):
        if not isinstance(item, dict):
            raise EvolutionError("benchmark task result must be an object")
        task_data = item.get("task")
        summary = item.get("summary")
        if not isinstance(task_data, dict) or task_data.get("task_id") != task_ref.task_id:
            raise EvolutionError(f"benchmark task order or id mismatch for {task_ref.task_id}")
        if not isinstance(summary, dict):
            raise EvolutionError(f"benchmark task {task_ref.task_id} has no run summary")
        branch = summary.get("branch")
        if not isinstance(branch, str) or not branch.startswith("apos/benchmark/"):
            raise EvolutionError(f"benchmark task {task_ref.task_id} has an invalid result branch")
        task_commit = _resolve_commit(trusted_git, branch)
        if not _is_ancestor(trusted_git, candidate_commit, task_commit):
            raise EvolutionError(f"benchmark branch for {task_ref.task_id} is outside candidate lineage")

        spec = TaskSpec.load(project_path(root, task_ref.path))
        command_results: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory(prefix="apos-replay-") as tmp:
            replay_root = Path(tmp)
            added = False
            try:
                trusted_git.run(["worktree", "add", "--detach", str(replay_root), task_commit])
                added = True
                environment = _candidate_environment(replay_root)
                for command in spec.test_commands:
                    command_result = _execute(command, replay_root, timeout_seconds, environment)
                    command_results.append(command_result)
                    if command_result["status"] != "PASS":
                        break
            finally:
                if added:
                    trusted_git.run(["worktree", "remove", "--force", str(replay_root)], check=False)
        replay_results.append(
            {
                "task_id": task_ref.task_id,
                "branch": branch,
                "commit": task_commit,
                "status": "PASS" if command_results and all(item["status"] == "PASS" for item in command_results) else "FAIL",
                "commands": command_results,
            }
        )
    replay_status = "PASS" if replay_results and all(item["status"] == "PASS" for item in replay_results) else "FAIL"
    return {"status": replay_status, "tasks": replay_results}


def _benchmark_evidence(payload: dict[str, object]) -> dict[str, object]:
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        raise EvolutionError("benchmark result has no summary")
    total = summary.get("total_tasks")
    passed = summary.get("passed_tasks")
    score = summary.get("average_quality_score")
    if not isinstance(total, int) or total < 1 or not isinstance(passed, int):
        raise EvolutionError("benchmark task counts are invalid")
    if not isinstance(score, int | float):
        raise EvolutionError("benchmark quality score is missing")
    return {
        "status": str(payload.get("status") or "UNKNOWN"),
        "result_id": payload.get("result_id"),
        "result_path": payload.get("result_path"),
        "total_tasks": total,
        "passed_tasks": passed,
        "pass_rate": round(passed / total, 4),
        "average_quality_score": float(score),
    }


def _gate(
    gates: list[dict[str, object]],
    name: str,
    passed: bool,
    detail: str,
    evidence: dict[str, object] | None = None,
) -> None:
    gate: dict[str, object] = {"name": name, "status": "PASS" if passed else "FAIL", "detail": detail}
    if evidence is not None:
        gate["evidence"] = evidence
    gates.append(gate)


def _review_markdown(report: dict[str, object]) -> str:
    gates = report.get("gates") if isinstance(report.get("gates"), list) else []
    changed = report.get("changed_files") if isinstance(report.get("changed_files"), list) else []
    lines = [
        f"# APOS Evolution Review: {report.get('candidate_id')}",
        "",
        f"- Status: {report.get('status')}",
        f"- Candidate commit: {report.get('candidate_commit')}",
        f"- Candidate version: {report.get('candidate_version')}",
        f"- Baseline: {report.get('baseline_ref')} ({report.get('baseline_commit')})",
        f"- Policy hash: {report.get('policy_hash')}",
        "- Automatic promotion: disabled",
        "",
        "## Gates",
        "",
    ]
    for gate in gates:
        if isinstance(gate, dict):
            lines.append(f"- {gate.get('status')}: {gate.get('name')} - {gate.get('detail')}")
    lines.extend(["", "## Changed Files", ""])
    lines.extend(f"- {path}" for path in changed)
    lines.extend(["", "## Diff Stat", "", "```text", str(report.get("diff_stat") or "(none)"), "```", ""])
    return "\n".join(lines)


def _mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise EvolutionError(f"{key} must be an object")
    return value


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EvolutionError(f"{key} is required")
    return value.strip()


def _string_list(data: dict[str, Any], key: str) -> list[str]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise EvolutionError(f"{key} must be a list of non-empty strings")
    return [item.strip() for item in value]


def _read_json(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise EvolutionError(f"{path} must contain a JSON object")
    return data


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _tail(value: str, max_chars: int = 6000) -> str:
    return value if len(value) <= max_chars else value[-max_chars:]


def _limit_output(value: str, max_chars: int | None) -> str:
    return value if max_chars is None else _tail(value, max_chars)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
