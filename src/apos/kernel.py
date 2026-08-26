from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path

from .coder import CommandPatchCoder, build_coder_prompt, summarize_failures
from .config import configured_coder_command, load_config
from .executor import run_commands
from .git import GitClient, GitError
from .models import AttemptResult, ContextRequest, PermissionSpec, RunSummary, TaskSpec
from .pathing import PathPolicyError, normalize_project_path
from .permissions import PermissionError, PermissionManager
from .runlog import RunRecorder


class KernelError(RuntimeError):
    """Raised when APOS cannot complete the task loop."""


@dataclass(frozen=True)
class RunOptions:
    coder_command: str | None = None
    max_attempts: int | None = None
    no_commit: bool = False
    allow_dirty: bool = False
    command_timeout_seconds: int | None = None
    approved_read: tuple[str, ...] = ()
    approved_write: tuple[str, ...] = ()
    denied_permissions: tuple[str, ...] = ()


class Kernel:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.git = GitClient(self.root)

    def run_task(self, spec: TaskSpec, options: RunOptions) -> RunSummary:
        self.root = self.git.ensure_repo().resolve()
        self.git = GitClient(self.root)
        config = load_config(self.root)
        defaults = config.get("defaults", {})

        dirty = self.git.status_porcelain()
        if dirty and not options.allow_dirty:
            raise KernelError("working tree is not clean; use --allow-dirty to override")

        branch = spec.branch or self.git.branch_name_for_task(
            spec.task_id,
            spec.display_title(),
            prefix=str(defaults.get("branch_prefix", "apos/task-")),
        )
        self.git.checkout_task_branch(branch)
        self.git.exclude_path(".apos/runs/")
        recorder = RunRecorder(self.root, spec, branch)

        timeout = int(options.command_timeout_seconds or defaults.get("command_timeout_seconds", 120))
        max_attempts = int(options.max_attempts or spec.max_attempts or defaults.get("max_attempts", 3))
        preflight_results = run_commands(spec.test_commands, cwd=self.root, timeout_seconds=timeout)
        if all(result.passed for result in preflight_results):
            recorder.record_tests(0, preflight_results)
            attempt = AttemptResult(0, "PASS", "tests already passed before coder changes", preflight_results)
            recorder.record_attempt(attempt)
            return self._finish(recorder, RunSummary("PASS", spec.task_id, branch, [attempt], committed=False))

        command = options.coder_command or configured_coder_command(self.root)
        if not command:
            raise KernelError("no Local Coder command configured; run apos connect or set APOS_CODER_COMMAND")

        coder = CommandPatchCoder(command=command, timeout_seconds=timeout)
        permissions = PermissionManager(PermissionSpec.from_task(spec))

        attempts: list[AttemptResult] = []
        previous_error: str | None = summarize_failures(preflight_results)

        for attempt_number in range(1, max_attempts + 1):
            prompt = build_coder_prompt(self.root, spec, attempt_number, previous_error)
            recorder.record_prompt(attempt_number, prompt)
            response = coder.run(prompt)
            recorder.record_response(
                attempt_number,
                response.type,
                response.patch,
                response.message,
                response.request,
            )

            if response.type == "request_permission":
                request = response.request
                message = "Local Coder requested permission"
                if request is not None:
                    message = f"{message}: {request.permission} {request.path} ({request.reason})"
                    decision = _existing_permission_decision(request, spec) or _permission_decision(request, options)
                    if decision == "deny":
                        attempt = AttemptResult(attempt_number, "PERMISSION_DENIED", f"{message}; denied by APOS run options")
                        attempts.append(attempt)
                        recorder.record_attempt(attempt)
                        return self._finish(recorder, RunSummary("PERMISSION_DENIED", spec.task_id, branch, attempts))
                    if decision in ("read", "write"):
                        spec = _grant_permission(spec, request.path, decision)
                        permissions = PermissionManager(PermissionSpec.from_task(spec))
                        previous_error = f"{message}; approved as {decision} for the next attempt"
                        attempt = AttemptResult(attempt_number, "PERMISSION_GRANTED", previous_error)
                        attempts.append(attempt)
                        recorder.record_attempt(attempt)
                        continue
                attempt = AttemptResult(attempt_number, "NEEDS_PERMISSION", message)
                attempts.append(attempt)
                recorder.record_attempt(attempt)
                return self._finish(recorder, RunSummary("NEEDS_PERMISSION", spec.task_id, branch, attempts))

            if response.type != "patch":
                message = response.message or "Local Coder did not return a patch"
                attempt = AttemptResult(attempt_number, "FAILED", message)
                attempts.append(attempt)
                recorder.record_attempt(attempt)
                previous_error = message
                continue

            changed_by_patch: list[str] = []
            patch_applied = False
            try:
                changed_by_patch = permissions.validate_patch(response.patch)
                self.git.apply_patch(response.patch)
                patch_applied = True
                permissions.validate_write_paths(changed_by_patch)
            except (PermissionError, GitError) as exc:
                message = str(exc)
                if patch_applied:
                    message = self._rollback_failed_patch(recorder, attempt_number, response.patch, message)
                attempt = AttemptResult(attempt_number, "FAILED", message)
                attempts.append(attempt)
                recorder.record_attempt(attempt)
                previous_error = message
                continue

            test_results = run_commands(spec.test_commands, cwd=self.root, timeout_seconds=timeout)
            recorder.record_tests(attempt_number, test_results)
            if all(result.passed for result in test_results):
                try:
                    permissions.validate_write_paths(changed_by_patch)
                except PermissionError as exc:
                    message = self._rollback_failed_patch(recorder, attempt_number, response.patch, str(exc))
                    attempt = AttemptResult(attempt_number, "FAILED", message, test_results)
                    attempts.append(attempt)
                    recorder.record_attempt(attempt)
                    previous_error = message
                    continue

                attempt = AttemptResult(
                    attempt_number,
                    "PASS",
                    f"tests passed after modifying {', '.join(changed_by_patch)}",
                    test_results,
                )
                attempts.append(attempt)
                recorder.record_attempt(attempt)
                if options.no_commit:
                    return self._finish(recorder, RunSummary("PASS", spec.task_id, branch, attempts, committed=False))
                commit_hash = self.git.commit(
                    changed_by_patch,
                    f"APOS {spec.task_id}: {spec.display_title()}",
                )
                return self._finish(
                    recorder,
                    RunSummary("PASS", spec.task_id, branch, attempts, committed=True, commit_hash=commit_hash),
                )

            previous_error = summarize_failures(test_results)
            previous_error = self._rollback_failed_patch(recorder, attempt_number, response.patch, previous_error)
            attempt = AttemptResult(attempt_number, "FAILED", previous_error, test_results)
            attempts.append(attempt)
            recorder.record_attempt(attempt)

        return self._finish(recorder, RunSummary("FAILED", spec.task_id, branch, attempts))

    def _finish(self, recorder: RunRecorder, summary: RunSummary) -> RunSummary:
        summary_with_log = RunSummary(
            status=summary.status,
            task_id=summary.task_id,
            branch=summary.branch,
            attempts=summary.attempts,
            committed=summary.committed,
            commit_hash=summary.commit_hash,
            run_log=recorder.relative_path(),
        )
        recorder.record_summary(summary_with_log)
        return summary_with_log

    def _rollback_failed_patch(self, recorder: RunRecorder, attempt: int, patch: str, message: str) -> str:
        try:
            self.git.reverse_patch(patch)
        except GitError as exc:
            rollback_message = f"rollback failed: {exc}"
            recorder.record_rollback(attempt, "FAILED", rollback_message)
            return f"{message}\n{rollback_message}"
        recorder.record_rollback(attempt, "PASS", "failed attempt patch was rolled back")
        return message


def _permission_decision(request: ContextRequest, options: RunOptions) -> str | None:
    try:
        requested_path = normalize_project_path(request.path)
        denied = {normalize_project_path(path) for path in options.denied_permissions}
        approved_read = {normalize_project_path(path) for path in options.approved_read}
        approved_write = {normalize_project_path(path) for path in options.approved_write}
    except PathPolicyError as exc:
        raise KernelError(str(exc)) from exc

    if requested_path in denied:
        return "deny"
    requested_permission = request.permission.lower()
    if requested_path in approved_write:
        return "write"
    if requested_permission == "read" and requested_path in approved_read:
        return "read"
    return None


def _existing_permission_decision(request: ContextRequest, spec: TaskSpec) -> str | None:
    try:
        requested_path = normalize_project_path(request.path)
        readable = {normalize_project_path(path) for path in spec.read_only_files + spec.allowed_files}
        writable = {normalize_project_path(path) for path in spec.allowed_files}
    except PathPolicyError as exc:
        raise KernelError(str(exc)) from exc

    requested_permission = request.permission.lower()
    if requested_path in writable:
        return "write"
    if requested_permission == "read" and requested_path in readable:
        return "read"
    return None


def _grant_permission(spec: TaskSpec, path: str, decision: str) -> TaskSpec:
    normalized_path = normalize_project_path(path)
    if decision == "write":
        allowed_files = _append_unique(spec.allowed_files, normalized_path)
        read_only_files = [item for item in spec.read_only_files if normalize_project_path(item) != normalized_path]
        return replace(spec, allowed_files=allowed_files, read_only_files=read_only_files)
    return replace(spec, read_only_files=_append_unique(spec.read_only_files, normalized_path))


def _append_unique(values: list[str], value: str) -> list[str]:
    normalized = normalize_project_path(value)
    seen = {normalize_project_path(item) for item in values}
    if normalized in seen:
        return list(values)
    return [*values, normalized]
