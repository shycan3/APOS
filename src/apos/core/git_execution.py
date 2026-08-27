from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shutil
from uuid import uuid4

from .execution import (
    CommandRequest,
    ControlledExecutionService,
    NetworkPolicy,
    ResourceLimits,
)
from .permissions import Actor, ApprovalSource, Capability
from .result import ErrorCode, ToolResult
from .tasks import ApprovalAction, TaskError, TaskService, TaskState
from .workspace import ProjectWorkspace


class GitExecutionError(RuntimeError):
    """Raised when a controlled Git operation cannot satisfy its contract."""


@dataclass(frozen=True)
class GitSnapshot:
    branch: str
    head: str | None
    dirty: bool

    def to_dict(self) -> dict[str, str | bool | None]:
        return {"branch": self.branch, "head": self.head, "dirty": self.dirty}


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

    def _snapshot(self) -> GitSnapshot:
        return GitSnapshot(
            branch=self.current_branch(),
            head=self._head(),
            dirty=bool(self.status_porcelain()),
        )

    def _run_read(self, *args: str, request_id: str | None = None) -> ToolResult[dict[str, object]]:
        request = self._request(
            args,
            capability=Capability.GIT_READ,
            request_id=request_id or uuid4().hex,
        )
        return self.service.execution.run(request)

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

    def _request(
        self,
        args: tuple[str, ...],
        *,
        capability: Capability,
        request_id: str,
        task_id: str | None = None,
    ) -> CommandRequest:
        return CommandRequest(
            executable=self.service.git_executable,
            args=(*self._git_safety_args(), *args),
            cwd="",
            environment=self._git_environment(),
            actor=self.actor,
            capability=capability,
            network_policy=NetworkPolicy.DENIED,
            limits=ResourceLimits(timeout_seconds=60, max_output_bytes_per_stream=64_000),
            request_id=request_id,
            task_id=task_id,
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

    @staticmethod
    def _message(result: ToolResult[dict[str, object]], fallback: str) -> str:
        if result.error is None:
            return fallback
        detail = result.error.details if isinstance(result.error.details, dict) else {}
        stderr = str(detail.get("stderr") or "").strip()
        stdout = str(detail.get("stdout") or "").strip()
        suffix = stderr or stdout or result.error.message
        return f"{fallback}: {suffix}"
