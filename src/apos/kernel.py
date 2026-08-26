from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .coder import CommandPatchCoder, build_coder_prompt, summarize_failures
from .config import configured_coder_command, load_config
from .executor import run_commands
from .git import GitClient, GitError
from .models import AttemptResult, PermissionSpec, RunSummary, TaskSpec
from .permissions import PermissionError, PermissionManager


class KernelError(RuntimeError):
    """Raised when APOS cannot complete the task loop."""


@dataclass(frozen=True)
class RunOptions:
    coder_command: str | None = None
    max_attempts: int | None = None
    no_commit: bool = False
    allow_dirty: bool = False
    command_timeout_seconds: int | None = None


class Kernel:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.git = GitClient(self.root)

    def run_task(self, spec: TaskSpec, options: RunOptions) -> RunSummary:
        self.root = self.git.ensure_repo().resolve()
        self.git = GitClient(self.root)
        config = load_config(self.root)
        defaults = config.get("defaults", {})

        command = options.coder_command or configured_coder_command(self.root)
        if not command:
            raise KernelError("no Local Coder command configured; run apos connect or set APOS_CODER_COMMAND")

        dirty = self.git.status_porcelain()
        if dirty and not options.allow_dirty:
            raise KernelError("working tree is not clean; use --allow-dirty to override")

        branch = spec.branch or self.git.branch_name_for_task(
            spec.task_id,
            spec.display_title(),
            prefix=str(defaults.get("branch_prefix", "apos/task-")),
        )
        self.git.checkout_task_branch(branch)

        timeout = int(options.command_timeout_seconds or defaults.get("command_timeout_seconds", 120))
        max_attempts = int(options.max_attempts or spec.max_attempts or defaults.get("max_attempts", 3))
        coder = CommandPatchCoder(command=command, timeout_seconds=timeout)
        permissions = PermissionManager(PermissionSpec.from_task(spec))

        attempts: list[AttemptResult] = []
        previous_error: str | None = None

        for attempt_number in range(1, max_attempts + 1):
            prompt = build_coder_prompt(self.root, spec, attempt_number, previous_error)
            response = coder.run(prompt)

            if response.type == "request_permission":
                request = response.request
                message = "Local Coder requested permission"
                if request is not None:
                    message = f"{message}: {request.permission} {request.path} ({request.reason})"
                attempts.append(AttemptResult(attempt_number, "NEEDS_PERMISSION", message))
                return RunSummary("NEEDS_PERMISSION", spec.task_id, branch, attempts)

            if response.type != "patch":
                message = response.message or "Local Coder did not return a patch"
                attempts.append(AttemptResult(attempt_number, "FAILED", message))
                previous_error = message
                continue

            try:
                changed_by_patch = permissions.validate_patch(response.patch)
                self.git.apply_patch(response.patch)
                changed_in_repo = self.git.changed_files()
                permissions.validate_write_paths(changed_in_repo)
            except (PermissionError, GitError) as exc:
                message = str(exc)
                attempts.append(AttemptResult(attempt_number, "FAILED", message))
                previous_error = message
                continue

            test_results = run_commands(spec.test_commands, cwd=self.root, timeout_seconds=timeout)
            if all(result.passed for result in test_results):
                try:
                    changed_in_repo = self.git.changed_files()
                    permissions.validate_write_paths(changed_in_repo)
                except PermissionError as exc:
                    message = str(exc)
                    attempts.append(AttemptResult(attempt_number, "FAILED", message, test_results))
                    previous_error = message
                    continue

                attempts.append(
                    AttemptResult(
                        attempt_number,
                        "PASS",
                        f"tests passed after modifying {', '.join(changed_by_patch)}",
                        test_results,
                    )
                )
                if options.no_commit:
                    return RunSummary("PASS", spec.task_id, branch, attempts, committed=False)
                commit_hash = self.git.commit(
                    changed_in_repo,
                    f"APOS {spec.task_id}: {spec.display_title()}",
                )
                return RunSummary("PASS", spec.task_id, branch, attempts, committed=True, commit_hash=commit_hash)

            previous_error = summarize_failures(test_results)
            attempts.append(AttemptResult(attempt_number, "FAILED", previous_error, test_results))

        return RunSummary("FAILED", spec.task_id, branch, attempts)
