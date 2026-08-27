from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .audit import AuditLog, Redactor
from .execution import CommandPolicy, ControlledExecutionService
from .filesystem import FileSystemService
from .git_execution import GitExecutionService
from .permissions import (
    AuthorizationService,
    Capability,
    Decision,
    PermissionEngine,
    PermissionPolicy,
    StaticPermissionPolicy,
)
from .tasks import (
    HumanApprovalBoundary,
    PersistentTask,
    SQLiteTaskRepository,
    TaskService,
)
from .test_execution import TestExecutionService
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
    git_execution: GitExecutionService
    test_execution: TestExecutionService
    validation: TaskSpecValidationService

    @classmethod
    def create_read_only(cls, root: Path) -> "ProjectRuntime":
        """Create the reviewed production profile for project-visible read operations."""

        return cls.create(
            root,
            permission_policy=StaticPermissionPolicy(
                {Capability.PROJECT_READ: Decision.ALLOW},
                policy_id="production-project-read-v1",
            ),
            command_policy=CommandPolicy.current_python(),
        )

    @classmethod
    def create_local_test_execution(cls, root: Path) -> "ProjectRuntime":
        """Create the local-only profile for persistent TaskSpec test execution."""

        return cls.create(
            root,
            permission_policy=StaticPermissionPolicy(
                {
                    Capability.PROJECT_READ: Decision.ALLOW,
                    Capability.TEST_EXECUTE: Decision.APPROVAL_REQUIRED,
                },
                policy_id="production-local-test-execution-v1",
            ),
            command_policy=CommandPolicy.current_python(),
        )

    @classmethod
    def create_local_git_phase_a(cls, root: Path) -> "ProjectRuntime":
        """Create the local-only profile for production Git read and branch prep."""

        return cls.create(
            root,
            permission_policy=StaticPermissionPolicy(
                {
                    Capability.GIT_READ: Decision.ALLOW,
                    Capability.GIT_WORKTREE_WRITE: Decision.APPROVAL_REQUIRED,
                    Capability.GIT_ROLLBACK: Decision.APPROVAL_REQUIRED,
                },
                policy_id="production-local-git-phase-a-v1",
            ),
            command_policy=CommandPolicy.current_git(),
        )

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
        git_execution = GitExecutionService(workspace, tasks, execution)
        test_execution = TestExecutionService(workspace, tasks, execution)
        return cls(
            workspace=workspace,
            permission_engine=permission_engine,
            authorization=authorization,
            audit_log=audit_log,
            filesystem=filesystem,
            execution=execution,
            tools=core_tool_registry(),
            tasks=tasks,
            git_execution=git_execution,
            test_execution=test_execution,
            validation=validation,
        )

    def recover_interrupted_tasks(self) -> tuple[PersistentTask, ...]:
        """Explicit recovery authority for a dedicated task execution owner at startup."""

        return self.tasks.recover_interrupted_tasks()
