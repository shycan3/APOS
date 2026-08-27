from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .audit import AuditLog, Redactor
from .execution import CommandPolicy, ControlledExecutionService
from .filesystem import FileSystemService
from .permissions import AuthorizationService, PermissionEngine, PermissionPolicy
from .tasks import (
    HumanApprovalBoundary,
    SQLiteTaskRepository,
    TaskService,
)
from .tools import ToolRegistry, core_tool_registry
from .validation import TaskSpecValidationService
from .workspace import ProjectWorkspace, SecretPolicy


@dataclass(frozen=True)
class ProjectRuntime:
    """One explicitly scoped composition root for future CLI, MCP, API, and GUI adapters."""

    workspace: ProjectWorkspace
    permission_engine: PermissionEngine
    authorization: AuthorizationService
    audit_log: AuditLog
    filesystem: FileSystemService
    execution: ControlledExecutionService
    tools: ToolRegistry
    tasks: TaskService
    validation: TaskSpecValidationService

    @classmethod
    def create(
        cls,
        root: Path,
        *,
        permission_policy: PermissionPolicy,
        command_policy: CommandPolicy,
        secret_policy: SecretPolicy | None = None,
        redactor: Redactor | None = None,
        task_store_path: Path | None = None,
        human_approval_boundary: HumanApprovalBoundary | None = None,
    ) -> "ProjectRuntime":
        workspace = ProjectWorkspace.register(root, secret_policy=secret_policy)
        shared_redactor = redactor or Redactor()
        audit_log = AuditLog(workspace, redactor=shared_redactor)
        task_repository = SQLiteTaskRepository(workspace, path=task_store_path)
        tasks = TaskService(
            workspace,
            task_repository,
            audit_log,
            human_approval_boundary=human_approval_boundary,
            redactor=shared_redactor,
        )
        permission_engine = PermissionEngine(permission_policy, approval_consumer=tasks)
        authorization = AuthorizationService(permission_engine, audit_log)
        filesystem = FileSystemService(workspace, authorization)
        execution = ControlledExecutionService(workspace, authorization, command_policy)
        validation = TaskSpecValidationService(filesystem)
        tasks._bind_execution_service(execution)
        return cls(
            workspace=workspace,
            permission_engine=permission_engine,
            authorization=authorization,
            audit_log=audit_log,
            filesystem=filesystem,
            execution=execution,
            tools=core_tool_registry(),
            tasks=tasks,
            validation=validation,
        )
