from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .audit import AuditLog, Redactor
from .execution import CommandPolicy, ControlledExecutionService
from .filesystem import FileSystemService
from .permissions import AuthorizationService, PermissionEngine, PermissionPolicy
from .tools import ToolRegistry, core_tool_registry
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

    @classmethod
    def create(
        cls,
        root: Path,
        *,
        permission_policy: PermissionPolicy,
        command_policy: CommandPolicy,
        secret_policy: SecretPolicy | None = None,
        redactor: Redactor | None = None,
    ) -> "ProjectRuntime":
        workspace = ProjectWorkspace.register(root, secret_policy=secret_policy)
        audit_log = AuditLog(workspace, redactor=redactor)
        permission_engine = PermissionEngine(permission_policy)
        authorization = AuthorizationService(permission_engine, audit_log)
        filesystem = FileSystemService(workspace, authorization)
        execution = ControlledExecutionService(workspace, authorization, command_policy)
        return cls(
            workspace=workspace,
            permission_engine=permission_engine,
            authorization=authorization,
            audit_log=audit_log,
            filesystem=filesystem,
            execution=execution,
            tools=core_tool_registry(),
        )
