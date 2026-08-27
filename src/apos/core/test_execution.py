from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from ..models import ExecutionResult
from .commandline import parse_legacy_command
from .execution import (
    CommandPreparationError,
    CommandRequest,
    ControlledExecutionService,
    NetworkPolicy,
    ResourceLimits,
)
from .permissions import Actor, ApprovalSource, Capability
from .result import ToolResult
from .tasks import ApprovalAction, TaskError, TaskService, TaskState
from .workspace import ProjectWorkspace, WorkspaceViolation


class TestExecutionService:
    """Persistent task facade for production TaskSpec test execution."""

    def __init__(
        self,
        workspace: ProjectWorkspace,
        tasks: TaskService,
        execution: ControlledExecutionService,
    ) -> None:
        if tasks.workspace.project_id != workspace.project_id:
            raise ValueError("task service belongs to a different project")
        if execution.workspace.project_id != workspace.project_id:
            raise ValueError("execution service belongs to a different project")
        self.workspace = workspace
        self.tasks = tasks
        self.execution = execution

    def bind(
        self,
        *,
        actor: Actor,
        approved_by: Actor | None,
    ) -> "TestExecutionSession":
        return TestExecutionSession(self, actor=actor, approved_by=approved_by)

    def cancel(self, request_id: str) -> bool:
        """Request process cancellation; task closure occurs when execution returns."""

        return self.execution.cancel(request_id)


@dataclass
class TestExecutionSession:
    service: TestExecutionService
    actor: Actor
    approved_by: Actor | None
    run_id: str = field(default_factory=lambda: uuid4().hex)
    _command_count: int = 0

    def run_commands(
        self,
        commands: list[str],
        *,
        cwd: Path,
        timeout_seconds: int,
        task_id: str,
    ) -> list[ExecutionResult]:
        results: list[ExecutionResult] = []
        for command in commands:
            self._command_count += 1
            result = self._run_command(
                command,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                parent_task_id=task_id,
                command_index=self._command_count,
            )
            results.append(result)
            if not result.passed:
                break
        return results

    def _run_command(
        self,
        command: str,
        *,
        cwd: Path,
        timeout_seconds: int,
        parent_task_id: str,
        command_index: int,
    ) -> ExecutionResult:
        try:
            arguments = parse_legacy_command(command)
        except ValueError as exc:
            return self._failure(command, "INVALID_ARGUMENT", str(exc))

        request_id = uuid4().hex
        execution_task_id = (
            f"{parent_task_id}:test:{self.run_id[:12]}:{command_index:03d}"
        )
        request = CommandRequest(
            executable=arguments[0],
            args=tuple(arguments[1:]),
            cwd=self._relative_cwd(cwd),
            actor=self.actor,
            capability=Capability.TEST_EXECUTE,
            network_policy=NetworkPolicy.DENIED,
            limits=ResourceLimits(timeout_seconds=timeout_seconds),
            request_id=request_id,
            task_id=execution_task_id,
        )

        try:
            self.service.tasks.create_command_task(
                request,
                description="Execute one TaskSpec test command",
                metadata={
                    "parent_taskspec_id": parent_task_id,
                    "run_correlation_id": self.run_id,
                    "command_index": command_index,
                },
            )
        except (CommandPreparationError, WorkspaceViolation) as exc:
            rejected = self.service.execution.run(request)
            if not rejected.success and rejected.error is not None:
                return self._from_tool_result(command, rejected)
            return self._failure(command, "COMMAND_NOT_ALLOWED", str(exc))
        except (TaskError, ValueError) as exc:
            return self._failure(command, "TASK_PERSISTENCE_FAILED", str(exc))

        self.service.tasks.queue_task(execution_task_id, actor=self.actor)
        self.service.tasks.request_approval(execution_task_id, actor=self.actor)
        permission_request = self.service.tasks.get_permission_request(execution_task_id)

        if self.approved_by is None:
            return self._failure(
                command,
                "PERMISSION_REQUIRED",
                "persistent human approval is required before test execution",
            )

        self.service.tasks.grant_approval(
            execution_task_id,
            action=ApprovalAction(
                request_id=permission_request.request_id,
                request_digest=permission_request.digest(),
                subject=self.actor,
                approved_by=self.approved_by,
                source=ApprovalSource.UNAUTHENTICATED_USER_REQUEST,
                note="Local apos run invocation approved this exact test command.",
            ),
        )
        result = self.service.tasks.run_command_task(request)

        current = self.service.tasks.get_task(execution_task_id)
        if not result.success and current.state == TaskState.APPROVED:
            self.service.tasks.cancel_task(
                execution_task_id,
                actor=self.actor,
                reason=result.error.message if result.error else "test execution denied",
            )
        return self._from_tool_result(command, result)

    def _relative_cwd(self, cwd: Path) -> str:
        resolved = cwd.resolve()
        try:
            relative = resolved.relative_to(self.service.workspace.root).as_posix()
        except ValueError:
            return str(resolved)
        return "" if relative == "." else relative

    @staticmethod
    def _from_tool_result(
        command: str,
        result: ToolResult[dict[str, object]],
    ) -> ExecutionResult:
        payload = result.data if result.success else (result.error.details if result.error else {})
        payload = payload if isinstance(payload, dict) else {}
        error_code = result.error.code.value if result.error else None
        exit_code = payload.get("exit_code")
        if not isinstance(exit_code, int):
            exit_code = 0 if result.success else 1
        return ExecutionResult(
            status="PASS" if result.success else "FAILED",
            stage="TEST",
            error_type=error_code,
            command=command,
            exit_code=exit_code,
            stdout=str(payload.get("stdout") or ""),
            stderr=str(payload.get("stderr") or ""),
            summary=("command passed" if result.success else result.error.message),
        )

    @staticmethod
    def _failure(command: str, error_type: str, message: str) -> ExecutionResult:
        return ExecutionResult(
            status="FAILED",
            stage="TEST",
            error_type=error_type,
            command=command,
            exit_code=1,
            summary=message,
        )
