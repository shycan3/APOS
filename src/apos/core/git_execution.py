from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import shutil
from uuid import uuid4

from .execution import (
    CommandRequest,
    ControlledExecutionService,
    NetworkPolicy,
    PreExecutionGuardError,
    ResourceLimits,
)
from .permissions import Actor, ApprovalSource, Capability
from .result import ErrorCode, ToolResult
from .tasks import ApprovalAction, TaskError, TaskService, TaskState
from .workspace import ProjectWorkspace


class GitExecutionError(RuntimeError):
    """Raised when a controlled Git operation cannot satisfy its contract."""

    def __init__(self, message: str, *, recovery_required: bool = False) -> None:
        super().__init__(message)
        self.recovery_required = recovery_required


@dataclass(frozen=True)
class GitSnapshot:
    branch: str
    head: str | None
    dirty: bool
    changed_files: tuple[str, ...] = ()
    staged_files: tuple[str, ...] = ()
    index_tree: str | None = None
    staged_diff_digest: str | None = None
    patch_digest: str | None = None
    expected_changed_paths: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "branch": self.branch,
            "head": self.head,
            "dirty": self.dirty,
            "changed_files": list(self.changed_files),
            "staged_files": list(self.staged_files),
            "index_tree": self.index_tree,
            "staged_diff_digest": self.staged_diff_digest,
            "patch_digest": self.patch_digest,
            "expected_changed_paths": list(self.expected_changed_paths),
        }


@dataclass
class GitExecutionService:
    """Modern control-plane facade for the migrated production Git phase."""

    workspace: ProjectWorkspace
    tasks: TaskService
    execution: ControlledExecutionService
    git_executable: str = field(default_factory=lambda: shutil.which("git") or "git")

    def bind(self, *, actor: Actor, approved_by: Actor | None) -> "GitExecutionSession":
        return GitExecutionSession(
            self,
            actor=actor,
            approved_by=approved_by,
        )


@dataclass
class GitExecutionSession:
    service: GitExecutionService
    actor: Actor
    approved_by: Actor | None

    def ensure_repo(self) -> Path:
        result = self._run_read("rev-parse", "--show-toplevel", request_id="git-read-ensure-repo")
        if not result.success:
            raise GitExecutionError(self._message(result, "git rev-parse --show-toplevel failed"))
        reported = Path(str(result.data.get("stdout", "")).strip())
        return reported if reported.exists() else self.service.workspace.root

    def has_commits(self) -> bool:
        result = self._run_read("rev-parse", "--verify", "HEAD")
        return result.success

    def current_branch(self) -> str:
        result = self._run_read("branch", "--show-current")
        if not result.success:
            raise GitExecutionError(self._message(result, "git branch --show-current failed"))
        branch = str(result.data.get("stdout", "")).strip()
        return branch or "HEAD"

    def status_porcelain(self) -> str:
        result = self._run_read("status", "--porcelain")
        if not result.success:
            raise GitExecutionError(self._message(result, "git status --porcelain failed"))
        return str(result.data.get("stdout", ""))

    def check_patch(self, patch: str) -> None:
        result = self._run_read(
            "apply",
            "--recount",
            "--ignore-space-change",
            "--check",
            "-",
            stdin_text=patch,
        )
        if not result.success:
            raise GitExecutionError(self._message(result, "git apply --check failed"))

    def apply_patch(self, patch: str) -> None:
        expected_paths = self._patch_paths(patch)
        patch_digest = self._patch_digest(patch)
        self.check_patch(patch)
        self._run_patch_mutation(
            patch,
            args=("apply", "--recount", "--ignore-space-change", "-"),
            capability=Capability.GIT_WORKTREE_WRITE,
            operation_name="apply_patch",
            expected_paths=expected_paths,
            patch_digest=patch_digest,
        )

    def check_reverse_patch(self, patch: str) -> None:
        result = self._run_read(
            "apply",
            "--reverse",
            "--recount",
            "--ignore-space-change",
            "--check",
            "-",
            stdin_text=patch,
        )
        if not result.success:
            raise GitExecutionError(self._message(result, "git apply --reverse --check failed"))

    def reverse_patch(self, patch: str) -> None:
        expected_paths = self._patch_paths(patch)
        patch_digest = self._patch_digest(patch)
        self.check_reverse_patch(patch)
        self._run_patch_mutation(
            patch,
            args=("apply", "--reverse", "--recount", "--ignore-space-change", "-"),
            capability=Capability.GIT_ROLLBACK,
            operation_name="reverse_patch",
            expected_paths=expected_paths,
            patch_digest=patch_digest,
        )

    def commit(
        self,
        files: list[str],
        message: str,
        *,
        patch_digest: str | None = None,
        task_id: str | None = None,
        attempt_number: int | None = None,
    ) -> str:
        expected_paths = tuple(sorted(dict.fromkeys(files)))
        if not expected_paths:
            raise GitExecutionError("no files to commit")
        if not message:
            raise GitExecutionError("commit message is required")

        before = self._snapshot(expected_paths=expected_paths, patch_digest=patch_digest)
        if before.staged_files:
            raise GitExecutionError(
                "pre-existing staged changes must be committed or unstaged before APOS can commit"
            )

        context = {
            "task_id": task_id,
            "attempt_number": attempt_number,
            "patch_digest": patch_digest,
            "expected_changed_paths": list(expected_paths),
            "commit_message_digest": self._text_digest(message),
        }
        add_task_id = f"git-index:{uuid4().hex}"
        add_result = self._run_git_task(
            ("add", "--", *expected_paths),
            capability=Capability.GIT_INDEX_WRITE,
            task_id=add_task_id,
            description="Stage APOS commit changes",
            metadata={
                "git_operation": "stage_commit_changes",
                "snapshot_before": before.to_dict(),
                **context,
            },
            semantic_metadata={
                "git_operation": "stage_commit_changes",
                "branch": before.branch,
                "head_before": before.head,
                **context,
            },
            approval_note="Local apos run invocation approved this exact Git index mutation.",
            approval_required_message="persistent human approval is required before Git index mutation",
        )
        try:
            after_add = self._snapshot(expected_paths=expected_paths, patch_digest=patch_digest)
        except Exception as exc:
            self._mark_git_recovery(add_task_id, f"git add state verification failed: {type(exc).__name__}")
            raise GitExecutionError("git add state verification failed", recovery_required=True) from exc

        if not add_result.success:
            if self._same_snapshot(before, after_add):
                self._complete_git_task(add_task_id, False, add_result)
                raise GitExecutionError(self._message(add_result, "git add failed"))
            self._mark_git_recovery(add_task_id, "git add failed after possible index mutation")
            raise GitExecutionError(
                self._message(add_result, "git add left index state ambiguous"),
                recovery_required=True,
            )

        if not self._verified_add_state(before, after_add, expected_paths):
            self._mark_git_recovery(add_task_id, "git add completed but staged state verification failed")
            raise GitExecutionError("git add completed but staged state verification failed", recovery_required=True)
        self._complete_git_task_or_recovery(
            add_task_id,
            True,
            add_result,
            "git add completed but task finalization failed",
        )

        diff_result = self._run_read("diff", "--cached", "--quiet")
        diff_exit_code = self._exit_code(diff_result)
        if diff_exit_code == 0:
            raise GitExecutionError("no staged changes to commit")
        if diff_exit_code != 1:
            try:
                after_diff = self._snapshot(expected_paths=expected_paths, patch_digest=patch_digest)
            except Exception as exc:
                raise GitExecutionError("cached diff state verification failed", recovery_required=True) from exc
            if self._same_snapshot(after_add, after_diff):
                raise GitExecutionError(self._message(diff_result, "git diff --cached --quiet failed"))
            raise GitExecutionError(
                self._message(diff_result, "git diff --cached --quiet left index state ambiguous"),
                recovery_required=True,
            )

        staged = self._snapshot(expected_paths=expected_paths, patch_digest=patch_digest)
        if staged.index_tree is None or staged.staged_diff_digest is None:
            raise GitExecutionError("staged state could not be verified", recovery_required=True)
        commit_context = {
            **context,
            "parent_head": staged.head,
            "branch": staged.branch,
            "staged_files": list(staged.staged_files),
            "staged_diff_digest": staged.staged_diff_digest,
            "index_tree": staged.index_tree,
        }
        commit_task_id = f"git-commit:{uuid4().hex}"
        commit_result = self._run_git_task(
            ("commit", "--no-verify", "-m", message),
            capability=Capability.GIT_REF_WRITE,
            task_id=commit_task_id,
            description="Commit APOS task changes",
            metadata={
                "git_operation": "commit_task_changes",
                "snapshot_before": staged.to_dict(),
                **commit_context,
            },
            semantic_metadata={
                "git_operation": "commit_task_changes",
                **commit_context,
            },
            pre_execution_guard=self._commit_drift_guard(staged, expected_paths),
            approval_note="Local apos run invocation approved this exact Git commit.",
            approval_required_message="persistent human approval is required before Git commit",
        )

        if not commit_result.success and self._pre_execution_guard_failed(commit_result):
            if self._pre_execution_guard_recovery_required(commit_result):
                self._mark_git_recovery(commit_task_id, "pre-commit state could not be verified")
                raise GitExecutionError(self._message(commit_result, "pre-commit drift guard failed"), recovery_required=True)
            self._complete_git_task(commit_task_id, False, commit_result)
            raise GitExecutionError(self._message(commit_result, "pre-commit drift guard rejected commit"))

        try:
            after_commit = self._snapshot(expected_paths=expected_paths, patch_digest=patch_digest)
        except Exception as exc:
            self._mark_git_recovery(commit_task_id, f"post-commit state verification failed: {type(exc).__name__}")
            raise GitExecutionError("post-commit state verification failed", recovery_required=True) from exc

        if not commit_result.success:
            if staged.head == after_commit.head and self._same_index_state(staged, after_commit):
                self._complete_git_task(commit_task_id, False, commit_result)
                raise GitExecutionError(self._message(commit_result, "git commit failed"))
            self._mark_git_recovery(commit_task_id, "git commit failed after possible ref or index mutation")
            raise GitExecutionError(
                self._message(commit_result, "git commit left repository state ambiguous"),
                recovery_required=True,
            )

        if self._verified_commit_state(staged, after_commit, expected_paths, message):
            self._complete_git_task_or_recovery(
                commit_task_id,
                True,
                commit_result,
                "git commit completed but task finalization failed",
            )
            if after_commit.head is None:
                raise GitExecutionError("post-commit HEAD could not be verified", recovery_required=True)
            return after_commit.head[:12]

        self._mark_git_recovery(commit_task_id, "git commit completed but post-state verification failed")
        raise GitExecutionError("git commit completed but post-state verification failed", recovery_required=True)

    def checkout_task_branch(self, branch: str) -> None:
        self._validate_branch_name(branch)
        if self.current_branch() == branch:
            self._verify_existing_branch(branch)
            return

        exists = self._branch_exists(branch)
        before = self._snapshot()
        args = ("checkout", branch) if exists else ("checkout", "-b", branch)
        task_id = f"git-branch:{uuid4().hex}"
        result = self._run_branch_task(
            args,
            task_id=task_id,
            metadata={
                "git_operation": "checkout_existing_branch" if exists else "create_task_branch",
                "target_branch": branch,
                "branch_exists_before": exists,
                "snapshot_before": before.to_dict(),
            },
        )

        if not result.success:
            current = self.service.tasks.get_task(task_id)
            if current.state == TaskState.RUNNING:
                self.service.tasks.complete_task(
                    task_id,
                    actor=self.actor,
                    succeeded=False,
                    failure_information={
                        "error_code": result.error.code.value if result.error else ErrorCode.INTERNAL_ERROR.value,
                        "message": result.error.message if result.error else "git branch preparation failed",
                    },
                )
            elif current.state == TaskState.APPROVED:
                self.service.tasks.complete_task(
                    task_id,
                    actor=self.actor,
                    succeeded=False,
                    failure_information={
                        "error_code": result.error.code.value if result.error else ErrorCode.INTERNAL_ERROR.value,
                        "message": result.error.message if result.error else "git branch preparation failed",
                    },
                )
            raise GitExecutionError(self._message(result, "git branch preparation failed"))

        try:
            after = self._snapshot()
            verified = after.branch == branch and self._branch_exists(branch)
        except Exception as exc:
            current = self.service.tasks.get_task(task_id)
            if current.state == TaskState.RUNNING:
                self.service.tasks.mark_recovery_required(
                    task_id,
                    actor=self.actor,
                    reason=f"git branch verification failed after checkout: {type(exc).__name__}",
                )
            raise GitExecutionError("git branch verification failed after checkout") from exc

        current = self.service.tasks.get_task(task_id)
        if current.state == TaskState.RUNNING:
            if verified:
                self.service.tasks.complete_task(task_id, actor=self.actor, succeeded=True)
            else:
                self.service.tasks.mark_recovery_required(
                    task_id,
                    actor=self.actor,
                    reason="git branch preparation completed but post-state did not match",
                )
                raise GitExecutionError("git branch preparation completed but post-state did not match")

    def branch_name_for_task(self, task_id: str, title: str, prefix: str = "apos/task-") -> str:
        from ..git import _slug

        normalized_id = _slug(task_id)
        if normalized_id.startswith("task-"):
            normalized_id = normalized_id.removeprefix("task-")
        normalized_title = _slug(title)
        return f"{prefix}{normalized_id}-{normalized_title}"

    def _verify_existing_branch(self, branch: str) -> None:
        if not self._branch_exists(branch):
            raise GitExecutionError(f"active branch is not a local branch: {branch}")

    def _validate_branch_name(self, branch: str) -> None:
        if (
            not branch
            or branch.startswith("-")
            or branch.startswith("@")
            or "@{" in branch
            or "\x00" in branch
            or branch.strip() != branch
        ):
            raise GitExecutionError("invalid branch name")
        result = self._run_read("check-ref-format", "--branch", branch)
        if not result.success:
            raise GitExecutionError(self._message(result, "invalid branch name"))

    def _branch_exists(self, branch: str) -> bool:
        result = self._run_read("show-ref", "--verify", "--quiet", f"refs/heads/{branch}")
        return result.success

    def _head(self) -> str | None:
        result = self._run_read("rev-parse", "--verify", "HEAD")
        if not result.success:
            return None
        return str(result.data.get("stdout", "")).strip() or None

    def _snapshot(
        self,
        *,
        patch_digest: str | None = None,
        expected_paths: tuple[str, ...] = (),
    ) -> GitSnapshot:
        status = self.status_porcelain()
        changed, staged = self._parse_status(status)
        return GitSnapshot(
            branch=self.current_branch(),
            head=self._head(),
            dirty=bool(status),
            changed_files=changed,
            staged_files=staged,
            index_tree=self._index_tree() if staged else None,
            staged_diff_digest=self._staged_diff_digest() if staged else None,
            patch_digest=patch_digest,
            expected_changed_paths=expected_paths,
        )

    def _run_read(
        self,
        *args: str,
        request_id: str | None = None,
        stdin_text: str | None = None,
    ) -> ToolResult[dict[str, object]]:
        request = self._request(
            args,
            capability=Capability.GIT_READ,
            request_id=request_id or uuid4().hex,
            stdin_text=stdin_text,
        )
        return self.service.execution.run(request)

    def _run_patch_mutation(
        self,
        patch: str,
        *,
        args: tuple[str, ...],
        capability: Capability,
        operation_name: str,
        expected_paths: tuple[str, ...],
        patch_digest: str,
    ) -> None:
        before = self._snapshot(patch_digest=patch_digest, expected_paths=expected_paths)
        task_id = f"git-patch:{uuid4().hex}"
        result = self._run_patch_task(
            args,
            patch,
            capability=capability,
            task_id=task_id,
            metadata={
                "git_operation": operation_name,
                "patch_digest": patch_digest,
                "target_branch": before.branch,
                "head_before": before.head,
                "expected_changed_paths": list(expected_paths),
                "snapshot_before": before.to_dict(),
            },
        )

        try:
            after = self._snapshot(patch_digest=patch_digest, expected_paths=expected_paths)
        except Exception as exc:
            self._mark_patch_recovery(task_id, f"patch state verification failed: {type(exc).__name__}")
            raise GitExecutionError("patch state verification failed", recovery_required=True) from exc

        if not result.success:
            if self._same_snapshot(before, after):
                self._complete_patch_task(task_id, False, result)
                raise GitExecutionError(self._message(result, f"git {operation_name} failed"))
            self._mark_patch_recovery(task_id, "git mutation failed and repository state changed or is ambiguous")
            raise GitExecutionError(
                self._message(result, f"git {operation_name} left repository state ambiguous"),
                recovery_required=True,
            )

        if self._verified_patch_state(before, after, expected_paths, reverse=(capability == Capability.GIT_ROLLBACK)):
            self._complete_patch_task(task_id, True, result)
            return

        self._mark_patch_recovery(task_id, "git mutation completed but repository state verification failed")
        raise GitExecutionError("git mutation completed but repository state verification failed", recovery_required=True)

    def _run_branch_task(
        self,
        args: tuple[str, ...],
        *,
        task_id: str,
        metadata: dict[str, object],
    ) -> ToolResult[dict[str, object]]:
        request = self._request(
            args,
            capability=Capability.GIT_WORKTREE_WRITE,
            request_id=uuid4().hex,
            task_id=task_id,
        )
        try:
            self.service.tasks.create_command_task(
                request,
                description="Prepare APOS task branch",
                metadata=metadata,
            )
            self.service.tasks.queue_task(task_id, actor=self.actor)
            self.service.tasks.request_approval(task_id, actor=self.actor)
        except TaskError:
            raise

        permission_request = self.service.tasks.get_permission_request(task_id)
        if self.approved_by is None:
            return ToolResult.fail(
                ErrorCode.PERMISSION_REQUIRED,
                "persistent human approval is required before Git branch preparation",
                details={"task_id": task_id},
            )

        self.service.tasks.grant_approval(
            task_id,
            action=ApprovalAction(
                request_id=permission_request.request_id,
                request_digest=permission_request.digest(),
                subject=self.actor,
                approved_by=self.approved_by,
                source=ApprovalSource.UNAUTHENTICATED_USER_REQUEST,
                note="Local apos run invocation approved this exact Git branch preparation.",
            ),
        )
        return self.service.tasks.run_command_task(request, close_on_result=False)

    def _run_patch_task(
        self,
        args: tuple[str, ...],
        patch: str,
        *,
        capability: Capability,
        task_id: str,
        metadata: dict[str, object],
    ) -> ToolResult[dict[str, object]]:
        request = self._request(
            args,
            capability=capability,
            request_id=uuid4().hex,
            task_id=task_id,
            stdin_text=patch,
        )
        self.service.tasks.create_command_task(
            request,
            description="Execute APOS patch Git mutation",
            metadata=metadata,
        )
        self.service.tasks.queue_task(task_id, actor=self.actor)
        self.service.tasks.request_approval(task_id, actor=self.actor)

        permission_request = self.service.tasks.get_permission_request(task_id)
        if self.approved_by is None:
            return ToolResult.fail(
                ErrorCode.PERMISSION_REQUIRED,
                "persistent human approval is required before Git patch mutation",
                details={"task_id": task_id},
            )

        self.service.tasks.grant_approval(
            task_id,
            action=ApprovalAction(
                request_id=permission_request.request_id,
                request_digest=permission_request.digest(),
                subject=self.actor,
                approved_by=self.approved_by,
                source=ApprovalSource.UNAUTHENTICATED_USER_REQUEST,
                note="Local apos run invocation approved this exact Git patch mutation.",
            ),
        )
        return self.service.tasks.run_command_task(request, close_on_result=False)

    def _run_git_task(
        self,
        args: tuple[str, ...],
        *,
        capability: Capability,
        task_id: str,
        description: str,
        metadata: dict[str, object],
        semantic_metadata: dict[str, object],
        approval_note: str,
        approval_required_message: str,
        pre_execution_guard=None,
    ) -> ToolResult[dict[str, object]]:
        request = self._request(
            args,
            capability=capability,
            request_id=uuid4().hex,
            task_id=task_id,
            semantic_metadata=semantic_metadata,
            pre_execution_guard=pre_execution_guard,
        )
        self.service.tasks.create_command_task(
            request,
            description=description,
            metadata=metadata,
        )
        self.service.tasks.queue_task(task_id, actor=self.actor)
        self.service.tasks.request_approval(task_id, actor=self.actor)

        permission_request = self.service.tasks.get_permission_request(task_id)
        if self.approved_by is None:
            return ToolResult.fail(
                ErrorCode.PERMISSION_REQUIRED,
                approval_required_message,
                details={"task_id": task_id},
            )

        self.service.tasks.grant_approval(
            task_id,
            action=ApprovalAction(
                request_id=permission_request.request_id,
                request_digest=permission_request.digest(),
                subject=self.actor,
                approved_by=self.approved_by,
                source=ApprovalSource.UNAUTHENTICATED_USER_REQUEST,
                note=approval_note,
            ),
        )
        return self.service.tasks.run_command_task(request, close_on_result=False)

    def _request(
        self,
        args: tuple[str, ...],
        *,
        capability: Capability,
        request_id: str,
        task_id: str | None = None,
        stdin_text: str | None = None,
        semantic_metadata: dict[str, object] | None = None,
        pre_execution_guard=None,
    ) -> CommandRequest:
        return CommandRequest(
            executable=self.service.git_executable,
            args=(*self._git_safety_args(), *args),
            cwd="",
            environment=self._git_environment(),
            stdin_text=stdin_text,
            actor=self.actor,
            capability=capability,
            network_policy=NetworkPolicy.DENIED,
            limits=ResourceLimits(timeout_seconds=60, max_output_bytes_per_stream=64_000),
            request_id=request_id,
            task_id=task_id,
            semantic_metadata=semantic_metadata or {},
            pre_execution_guard=pre_execution_guard,
        )

    def _git_safety_args(self) -> tuple[str, ...]:
        hooks = self.service.workspace.root / ".apos" / "tmp" / "git-hooks-disabled"
        hooks.mkdir(parents=True, exist_ok=True)
        return (
            "-c",
            f"core.hooksPath={hooks}",
            "-c",
            "credential.helper=",
            "-c",
            "protocol.file.allow=never",
        )

    def _git_environment(self) -> dict[str, str]:
        return {}

    def _complete_patch_task(self, task_id: str, succeeded: bool, result: ToolResult[dict[str, object]]) -> None:
        current = self.service.tasks.get_task(task_id)
        if current.state == TaskState.RUNNING:
            self.service.tasks.complete_task(
                task_id,
                actor=self.actor,
                succeeded=succeeded,
                failure_information=None if succeeded else {
                    "error_code": result.error.code.value if result.error else ErrorCode.INTERNAL_ERROR.value,
                    "message": result.error.message if result.error else "git patch mutation failed",
                },
            )
        elif current.state == TaskState.APPROVED:
            self.service.tasks.complete_task(
                task_id,
                actor=self.actor,
                succeeded=False,
                failure_information={
                    "error_code": result.error.code.value if result.error else ErrorCode.INTERNAL_ERROR.value,
                    "message": result.error.message if result.error else "git patch mutation failed",
                },
            )

    def _mark_patch_recovery(self, task_id: str, reason: str) -> None:
        try:
            current = self.service.tasks.get_task(task_id)
        except TaskError:
            return
        if current.state == TaskState.RUNNING:
            try:
                self.service.tasks.mark_recovery_required(task_id, actor=self.actor, reason=reason)
            except TaskError:
                return
        elif current.state == TaskState.APPROVED:
            try:
                self.service.tasks.complete_task(
                    task_id,
                    actor=self.actor,
                    succeeded=False,
                    failure_information={"error_code": ErrorCode.INTERNAL_ERROR.value, "message": reason},
                )
            except TaskError:
                return

    def _complete_git_task(self, task_id: str, succeeded: bool, result: ToolResult[dict[str, object]]) -> None:
        self._complete_patch_task(task_id, succeeded, result)

    def _complete_git_task_or_recovery(
        self,
        task_id: str,
        succeeded: bool,
        result: ToolResult[dict[str, object]],
        recovery_message: str,
    ) -> None:
        try:
            self._complete_git_task(task_id, succeeded, result)
        except TaskError as exc:
            self._mark_git_recovery(task_id, recovery_message)
            raise GitExecutionError(recovery_message, recovery_required=True) from exc

    def _mark_git_recovery(self, task_id: str, reason: str) -> None:
        self._mark_patch_recovery(task_id, reason)

    def _commit_drift_guard(self, approved: GitSnapshot, expected_paths: tuple[str, ...]):
        def guard() -> None:
            try:
                live = self._snapshot(
                    expected_paths=expected_paths,
                    patch_digest=approved.patch_digest,
                )
            except Exception as exc:
                raise PreExecutionGuardError(
                    "pre-commit state could not be verified",
                    recovery_required=True,
                    details={"reason": type(exc).__name__},
                ) from exc
            if (
                live.branch != approved.branch
                or live.head != approved.head
                or live.staged_files != approved.staged_files
                or live.staged_diff_digest != approved.staged_diff_digest
                or live.index_tree != approved.index_tree
            ):
                raise PreExecutionGuardError(
                    "pre-commit state drifted after approval",
                    details={
                        "approved_branch": approved.branch,
                        "live_branch": live.branch,
                        "approved_head": approved.head,
                        "live_head": live.head,
                        "approved_staged_files": list(approved.staged_files),
                        "live_staged_files": list(live.staged_files),
                        "approved_staged_diff_digest": approved.staged_diff_digest,
                        "live_staged_diff_digest": live.staged_diff_digest,
                        "approved_index_tree": approved.index_tree,
                        "live_index_tree": live.index_tree,
                    },
                )

        return guard

    @staticmethod
    def _pre_execution_guard_failed(result: ToolResult[dict[str, object]]) -> bool:
        if result.error is None:
            return False
        return result.error.details.get("pre_execution_guard") == "failed"

    @staticmethod
    def _pre_execution_guard_recovery_required(result: ToolResult[dict[str, object]]) -> bool:
        if result.error is None:
            return False
        return bool(result.error.details.get("recovery_required"))

    def _index_tree(self) -> str | None:
        result = self._run_read("write-tree")
        if not result.success:
            return None
        return str(result.data.get("stdout", "")).strip() or None

    def _staged_diff_digest(self) -> str | None:
        result = self._run_read("diff", "--cached", "--full-index", "--binary")
        if not result.success:
            return None
        return self._text_digest(str(result.data.get("stdout", "")))

    def _commit_parent(self, commit: str) -> str | None:
        result = self._run_read("rev-list", "--parents", "-n", "1", commit)
        if not result.success:
            return None
        parts = str(result.data.get("stdout", "")).strip().split()
        if len(parts) != 2 or parts[0] != commit:
            return None
        return parts[1]

    def _commit_tree(self, commit: str) -> str | None:
        result = self._run_read("rev-parse", f"{commit}^{{tree}}")
        if not result.success:
            return None
        return str(result.data.get("stdout", "")).strip() or None

    def _commit_message(self, commit: str) -> str | None:
        result = self._run_read("log", "-1", "--format=%B", commit)
        if not result.success:
            return None
        return str(result.data.get("stdout", "")).rstrip("\n")

    @staticmethod
    def _exit_code(result: ToolResult[dict[str, object]]) -> int | None:
        if result.success and result.data is not None:
            return int(result.data.get("exit_code", 0))
        if result.error is not None:
            detail = result.error.details if isinstance(result.error.details, dict) else {}
            exit_code = detail.get("exit_code")
            return int(exit_code) if exit_code is not None else None
        return None

    @staticmethod
    def _same_snapshot(before: GitSnapshot, after: GitSnapshot) -> bool:
        return (
            before.branch == after.branch
            and before.head == after.head
            and before.changed_files == after.changed_files
            and before.staged_files == after.staged_files
            and before.index_tree == after.index_tree
            and before.staged_diff_digest == after.staged_diff_digest
        )

    @staticmethod
    def _same_index_state(before: GitSnapshot, after: GitSnapshot) -> bool:
        return (
            before.staged_files == after.staged_files
            and before.index_tree == after.index_tree
            and before.staged_diff_digest == after.staged_diff_digest
        )

    @staticmethod
    def _verified_add_state(before: GitSnapshot, after: GitSnapshot, expected_paths: tuple[str, ...]) -> bool:
        expected = set(expected_paths)
        staged = set(after.staged_files)
        return (
            before.branch == after.branch
            and before.head == after.head
            and not before.staged_files
            and bool(staged)
            and staged.issubset(expected)
            and after.index_tree is not None
            and after.staged_diff_digest is not None
        )

    def _verified_commit_state(
        self,
        before: GitSnapshot,
        after: GitSnapshot,
        expected_paths: tuple[str, ...],
        message: str,
    ) -> bool:
        if before.branch != after.branch:
            return False
        if before.head is None or after.head is None or before.head == after.head:
            return False
        if self._commit_parent(after.head) != before.head:
            return False
        if self._commit_tree(after.head) != before.index_tree:
            return False
        if self._commit_message(after.head) != message:
            return False
        if set(after.staged_files) & set(expected_paths):
            return False
        return True

    @staticmethod
    def _verified_patch_state(
        before: GitSnapshot,
        after: GitSnapshot,
        expected_paths: tuple[str, ...],
        *,
        reverse: bool,
    ) -> bool:
        if before.branch != after.branch or before.head != after.head:
            return False
        before_changed = set(before.changed_files)
        after_changed = set(after.changed_files)
        expected = set(expected_paths)
        if set(after.staged_files) != set(before.staged_files):
            return False
        if reverse:
            return after_changed.isdisjoint(expected) and after_changed.issubset(before_changed)
        return expected.issubset(after_changed) and after_changed.issubset(before_changed | expected)

    @staticmethod
    def _parse_status(status: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        changed: set[str] = set()
        staged: set[str] = set()
        for line in status.splitlines():
            if len(line) < 4:
                continue
            index_status = line[0]
            worktree_status = line[1]
            path_text = line[3:]
            paths = [path_text]
            if " -> " in path_text:
                paths = path_text.split(" -> ", 1)
            for path in paths:
                cleaned = path.strip().strip('"')
                if cleaned:
                    changed.add(cleaned)
                    if index_status not in {" ", "?"}:
                        staged.add(cleaned)
            if worktree_status not in {" ", "?"}:
                changed.update(path.strip().strip('"') for path in paths if path.strip())

        return tuple(sorted(changed)), tuple(sorted(staged))

    @staticmethod
    def _patch_digest(patch: str) -> str:
        return hashlib.sha256(patch.encode("utf-8")).hexdigest()

    @staticmethod
    def _text_digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _patch_paths(patch: str) -> tuple[str, ...]:
        from ..permissions import _extract_patch_paths

        return tuple(sorted(_extract_patch_paths(patch)))

    @staticmethod
    def _message(result: ToolResult[dict[str, object]], fallback: str) -> str:
        if result.error is None:
            return fallback
        detail = result.error.details if isinstance(result.error.details, dict) else {}
        stderr = str(detail.get("stderr") or "").strip()
        stdout = str(detail.get("stdout") or "").strip()
        suffix = stderr or stdout or result.error.message
        return f"{fallback}: {suffix}"
