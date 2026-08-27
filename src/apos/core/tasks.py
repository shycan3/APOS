from __future__ import annotations

from dataclasses import dataclass, field
from contextlib import closing
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import sqlite3
from typing import Any, Protocol, TYPE_CHECKING
from uuid import uuid4

from .audit import AuditLog, AuditStatus, Redactor
from .permissions import (
    Actor,
    ActorKind,
    ApprovalConsumptionResult,
    ApprovalGrant,
    ApprovalSource,
    Capability,
    PermissionRequest,
    RiskLevel,
)
from .result import ErrorCode
from .workspace import ProjectWorkspace

if TYPE_CHECKING:
    from .execution import CommandRequest, ControlledExecutionService


SCHEMA_VERSION = 1


class TaskState(str, Enum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class TaskError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class TaskStateMachine:
    _ALLOWED: dict[TaskState, frozenset[TaskState]] = {
        TaskState.CREATED: frozenset({TaskState.QUEUED, TaskState.CANCELLED, TaskState.EXPIRED}),
        TaskState.QUEUED: frozenset({TaskState.WAITING_APPROVAL, TaskState.CANCELLED, TaskState.EXPIRED}),
        TaskState.WAITING_APPROVAL: frozenset(
            {TaskState.APPROVED, TaskState.CANCELLED, TaskState.EXPIRED}
        ),
        TaskState.APPROVED: frozenset({TaskState.RUNNING, TaskState.CANCELLED, TaskState.EXPIRED}),
        TaskState.RUNNING: frozenset(
            {
                TaskState.SUCCEEDED,
                TaskState.FAILED,
                TaskState.CANCELLED,
                TaskState.RECOVERY_REQUIRED,
            }
        ),
        TaskState.RECOVERY_REQUIRED: frozenset({TaskState.FAILED, TaskState.CANCELLED}),
        TaskState.SUCCEEDED: frozenset(),
        TaskState.FAILED: frozenset(),
        TaskState.CANCELLED: frozenset(),
        TaskState.EXPIRED: frozenset(),
    }

    @classmethod
    def validate(cls, current: TaskState, target: TaskState) -> None:
        if target not in cls._ALLOWED[current]:
            raise TaskError(
                ErrorCode.INVALID_TASK_TRANSITION,
                f"task transition is not allowed: {current.value} -> {target.value}",
            )

    @classmethod
    def allowed_targets(cls, current: TaskState) -> frozenset[TaskState]:
        return cls._ALLOWED[current]


@dataclass(frozen=True)
class PersistentTask:
    task_id: str
    project_id: str
    created_at: str
    updated_at: str
    actor: Actor
    description: str
    requested_capability: Capability
    state: TaskState
    permission_request_id: str
    approval_grant_id: str | None = None
    retry_count: int = 0
    timestamps: dict[str, str] = field(default_factory=dict)
    failure_information: dict[str, Any] | None = None
    cancellation_information: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    version: int = 0


@dataclass(frozen=True)
class PersistentApproval:
    approval_id: str
    task_id: str
    request_id: str
    project_id: str
    request_digest: str
    subject: Actor
    approved_by: Actor
    note: str
    source: ApprovalSource
    authenticated: bool
    issued_at: str
    expires_at: str | None
    consumed_at: str | None = None
    consumed_by_task_id: str | None = None

    def to_grant(self) -> ApprovalGrant:
        return ApprovalGrant(
            request_id=self.request_id,
            project_id=self.project_id,
            request_digest=self.request_digest,
            approved_by=self.approved_by,
            note=self.note,
            grant_id=self.approval_id,
            approval_source=self.source,
            authenticated=self.authenticated,
            issued_at=self.issued_at,
            expires_at=self.expires_at,
        )


@dataclass(frozen=True)
class ApprovalAction:
    request_id: str
    request_digest: str
    subject: Actor
    approved_by: Actor
    source: ApprovalSource
    note: str
    expires_at: str | None = None
    authenticated: bool = False


class HumanApprovalBoundary(Protocol):
    authentication_implemented: bool

    def validate(self, action: ApprovalAction) -> None:
        ...


class LocalUnauthenticatedHumanApprovalBoundary:
    """Explicit local-only boundary; it never claims authenticated human identity."""

    authentication_implemented = False

    def validate(self, action: ApprovalAction) -> None:
        if action.source == ApprovalSource.AUTHENTICATED_HUMAN or action.authenticated:
            raise TaskError(
                ErrorCode.HUMAN_AUTHENTICATION_UNIMPLEMENTED,
                "authenticated human approval is not implemented",
            )
        if action.source != ApprovalSource.UNAUTHENTICATED_USER_REQUEST:
            raise TaskError(
                ErrorCode.PERMISSION_DENIED,
                "only an explicit unauthenticated local user request is supported",
            )
        if action.approved_by.kind != ActorKind.USER:
            raise TaskError(
                ErrorCode.PERMISSION_DENIED,
                "AI and SYSTEM actors cannot issue human approval",
            )
        if not action.note.strip():
            raise TaskError(ErrorCode.INVALID_ARGUMENT, "approval note is required")


@dataclass(frozen=True)
class TaskOutboxEvent:
    event_id: str
    event_type: str
    task_id: str
    request_id: str
    approval_id: str | None
    actor: Actor
    capability: Capability
    metadata: dict[str, Any]
    created_at: str


class TaskRepository(Protocol):
    path: Path

    def initialize(self) -> None:
        ...

    def create(self, task: PersistentTask, request: PermissionRequest) -> PersistentTask:
        ...

    def get_task(self, task_id: str) -> PersistentTask:
        ...

    def get_permission_request(self, request_id: str) -> PermissionRequest:
        ...

    def get_approval(self, approval_id: str) -> PersistentApproval:
        ...

    def transition(
        self,
        task_id: str,
        target: TaskState,
        *,
        actor: Actor,
        event_types: tuple[str, ...],
        failure_information: dict[str, Any] | None = None,
        cancellation_information: dict[str, Any] | None = None,
    ) -> PersistentTask:
        ...

    def issue_approval(
        self,
        task_id: str,
        approval: PersistentApproval,
    ) -> tuple[PersistentTask, PersistentApproval]:
        ...

    def consume_approval(
        self,
        request: PermissionRequest,
        grant: ApprovalGrant,
    ) -> ApprovalConsumptionResult:
        ...

    def recover_running(self, actor: Actor) -> tuple[PersistentTask, ...]:
        ...

    def pending_events(self) -> tuple[TaskOutboxEvent, ...]:
        ...

    def mark_event_published(self, event_id: str, audit_event_id: str) -> None:
        ...


class SQLiteTaskRepository:
    """SQLite-backed task store with transactional state and approval consumption."""

    def __init__(
        self,
        workspace: ProjectWorkspace,
        *,
        path: Path | None = None,
    ) -> None:
        self.workspace = workspace
        candidate = (path or (workspace.root / ".apos" / "state" / "tasks.sqlite3")).resolve(
            strict=False
        )
        try:
            candidate.relative_to(workspace.root)
        except ValueError as exc:
            raise TaskError(
                ErrorCode.PATH_OUTSIDE_PROJECT,
                "task store must be located inside the project root",
            ) from exc
        self.path = candidate

    def initialize(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version == 0:
                    self._create_schema(connection)
                    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                elif version != SCHEMA_VERSION:
                    raise TaskError(
                        ErrorCode.PERSISTENCE_CORRUPTED,
                        f"unsupported task store schema version: {version}",
                    )
                integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
                if integrity != "ok":
                    raise TaskError(
                        ErrorCode.PERSISTENCE_CORRUPTED,
                        f"task store integrity check failed: {integrity}",
                    )
        except TaskError:
            raise
        except sqlite3.DatabaseError as exc:
            raise TaskError(
                ErrorCode.PERSISTENCE_CORRUPTED,
                f"task store is unreadable or corrupted: {exc}",
            ) from exc
        except OSError as exc:
            raise TaskError(
                ErrorCode.IO_ERROR,
                f"task store initialization failed: {type(exc).__name__}",
            ) from exc

    def create(self, task: PersistentTask, request: PermissionRequest) -> PersistentTask:
        try:
            with self._transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO permission_requests (
                        request_id, task_id, project_id, actor_kind, actor_id, capability,
                        resource, operation, risk_level, metadata_json, request_digest, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request.request_id,
                        task.task_id,
                        request.project_id,
                        request.actor.kind.value,
                        request.actor.actor_id,
                        request.capability.value,
                        request.resource,
                        request.operation,
                        request.risk_level.name,
                        _json_dump(request.metadata),
                        request.digest(),
                        task.created_at,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO tasks (
                        task_id, project_id, created_at, updated_at, actor_kind, actor_id,
                        description, requested_capability, state, permission_request_id,
                        approval_grant_id, retry_count, timestamps_json, failure_json,
                        cancellation_json, metadata_json, version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task.task_id,
                        task.project_id,
                        task.created_at,
                        task.updated_at,
                        task.actor.kind.value,
                        task.actor.actor_id,
                        task.description,
                        task.requested_capability.value,
                        task.state.value,
                        task.permission_request_id,
                        task.approval_grant_id,
                        task.retry_count,
                        _json_dump(task.timestamps),
                        _json_dump(task.failure_information),
                        _json_dump(task.cancellation_information),
                        _json_dump(task.metadata),
                        task.version,
                    ),
                )
                self._insert_event(
                    connection,
                    event_type="TASK_CREATED",
                    task=task,
                    actor=task.actor,
                    metadata={"state": task.state.value},
                )
        except sqlite3.IntegrityError as exc:
            raise TaskError(
                ErrorCode.TASK_ALREADY_EXISTS,
                f"task or permission request already exists: {task.task_id}",
            ) from exc
        return self.get_task(task.task_id)

    def get_task(self, task_id: str) -> PersistentTask:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        if row is None:
            raise TaskError(ErrorCode.TASK_NOT_FOUND, f"task does not exist: {task_id}")
        return self._task_from_row(row)

    def get_permission_request(self, request_id: str) -> PermissionRequest:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT * FROM permission_requests WHERE request_id = ?", (request_id,)
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        if row is None:
            raise TaskError(ErrorCode.PERMISSION_DENIED, f"permission request does not exist: {request_id}")
        request = PermissionRequest(
            request_id=row["request_id"],
            project_id=row["project_id"],
            actor=Actor(ActorKind(row["actor_kind"]), row["actor_id"]),
            capability=Capability(row["capability"]),
            resource=row["resource"],
            operation=row["operation"],
            risk_level=RiskLevel[row["risk_level"]],
            metadata=_json_load(row["metadata_json"], {}),
            task_id=row["task_id"],
        )
        if request.digest() != row["request_digest"]:
            raise TaskError(
                ErrorCode.PERSISTENCE_CORRUPTED,
                f"permission request digest mismatch: {request_id}",
            )
        return request

    def get_approval(self, approval_id: str) -> PersistentApproval:
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        if row is None:
            raise TaskError(ErrorCode.APPROVAL_NOT_FOUND, f"approval does not exist: {approval_id}")
        return self._approval_from_row(row)

    def transition(
        self,
        task_id: str,
        target: TaskState,
        *,
        actor: Actor,
        event_types: tuple[str, ...],
        failure_information: dict[str, Any] | None = None,
        cancellation_information: dict[str, Any] | None = None,
    ) -> PersistentTask:
        if target in {TaskState.APPROVED, TaskState.RUNNING}:
            raise TaskError(
                ErrorCode.INVALID_TASK_TRANSITION,
                f"{target.value} requires its dedicated approval transaction",
            )
        with self._transaction() as connection:
            task = self._task_for_update(connection, task_id)
            TaskStateMachine.validate(task.state, target)
            now = _utc_now()
            timestamps = dict(task.timestamps)
            timestamps[target.value] = now
            updated = connection.execute(
                """
                UPDATE tasks
                SET state = ?, updated_at = ?, timestamps_json = ?, failure_json = ?,
                    cancellation_json = ?, version = version + 1
                WHERE task_id = ? AND version = ?
                """,
                (
                    target.value,
                    now,
                    _json_dump(timestamps),
                    _json_dump(failure_information),
                    _json_dump(cancellation_information),
                    task_id,
                    task.version,
                ),
            )
            if updated.rowcount != 1:
                raise TaskError(ErrorCode.CONCURRENT_MODIFICATION, "task was modified concurrently")
            changed = self._task_for_update(connection, task_id)
            for event_type in event_types:
                self._insert_event(
                    connection,
                    event_type=event_type,
                    task=changed,
                    actor=actor,
                    metadata={"from_state": task.state.value, "to_state": target.value},
                )
        return self.get_task(task_id)

    def issue_approval(
        self,
        task_id: str,
        approval: PersistentApproval,
    ) -> tuple[PersistentTask, PersistentApproval]:
        try:
            with self._transaction() as connection:
                task = self._task_for_update(connection, task_id)
                TaskStateMachine.validate(task.state, TaskState.APPROVED)
                if task.permission_request_id != approval.request_id:
                    raise TaskError(ErrorCode.PERMISSION_DENIED, "approval request does not belong to task")
                connection.execute(
                    """
                    INSERT INTO approvals (
                        approval_id, task_id, request_id, project_id, request_digest,
                        subject_kind, subject_id, approved_by_kind, approved_by_id, note,
                        source, authenticated, issued_at, expires_at, consumed_at,
                        consumed_by_task_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        approval.approval_id,
                        approval.task_id,
                        approval.request_id,
                        approval.project_id,
                        approval.request_digest,
                        approval.subject.kind.value,
                        approval.subject.actor_id,
                        approval.approved_by.kind.value,
                        approval.approved_by.actor_id,
                        approval.note,
                        approval.source.value,
                        int(approval.authenticated),
                        approval.issued_at,
                        approval.expires_at,
                        approval.consumed_at,
                        approval.consumed_by_task_id,
                    ),
                )
                now = _utc_now()
                timestamps = dict(task.timestamps)
                timestamps[TaskState.APPROVED.value] = now
                updated = connection.execute(
                    """
                    UPDATE tasks
                    SET state = ?, approval_grant_id = ?, updated_at = ?, timestamps_json = ?,
                        version = version + 1
                    WHERE task_id = ? AND version = ?
                    """,
                    (
                        TaskState.APPROVED.value,
                        approval.approval_id,
                        now,
                        _json_dump(timestamps),
                        task_id,
                        task.version,
                    ),
                )
                if updated.rowcount != 1:
                    raise TaskError(ErrorCode.CONCURRENT_MODIFICATION, "task was modified concurrently")
                changed = self._task_for_update(connection, task_id)
                self._insert_event(
                    connection,
                    event_type="APPROVAL_GRANTED",
                    task=changed,
                    actor=approval.approved_by,
                    approval_id=approval.approval_id,
                    metadata={
                        "source": approval.source.value,
                        "authenticated": approval.authenticated,
                        "expires_at": approval.expires_at,
                    },
                )
                self._insert_event(
                    connection,
                    event_type="TASK_APPROVED",
                    task=changed,
                    actor=approval.approved_by,
                    approval_id=approval.approval_id,
                    metadata={"from_state": task.state.value, "to_state": TaskState.APPROVED.value},
                )
        except sqlite3.IntegrityError as exc:
            raise TaskError(ErrorCode.PERMISSION_DENIED, "approval already exists for task or request") from exc
        return self.get_task(task_id), self.get_approval(approval.approval_id)

    def consume_approval(
        self,
        request: PermissionRequest,
        grant: ApprovalGrant,
    ) -> ApprovalConsumptionResult:
        if not grant.grant_id:
            return ApprovalConsumptionResult(
                False,
                "persistent approval grant ID is required",
                ErrorCode.APPROVAL_NOT_FOUND,
            )
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE approval_id = ?", (grant.grant_id,)
            ).fetchone()
            if row is None:
                return ApprovalConsumptionResult(
                    False, "persistent approval does not exist", ErrorCode.APPROVAL_NOT_FOUND
                )
            approval = self._approval_from_row(row)
            task = self._task_for_update(connection, approval.task_id)
            stored_request = self._request_for_update(connection, approval.request_id)

            mismatch = self._approval_mismatch(task, stored_request, request, approval, grant)
            if mismatch:
                return ApprovalConsumptionResult(
                    False, mismatch, ErrorCode.APPROVAL_SUBJECT_MISMATCH
                )
            if approval.consumed_at is not None:
                return ApprovalConsumptionResult(
                    False, "approval was already consumed", ErrorCode.APPROVAL_ALREADY_CONSUMED
                )
            if task.state != TaskState.APPROVED:
                return ApprovalConsumptionResult(
                    False,
                    f"task is not ready for approval consumption: {task.state.value}",
                    ErrorCode.INVALID_TASK_TRANSITION,
                )

            now = _utc_now()
            if approval.expires_at is not None and _parse_time(approval.expires_at) <= _parse_time(now):
                timestamps = dict(task.timestamps)
                timestamps[TaskState.EXPIRED.value] = now
                connection.execute(
                    """
                    UPDATE tasks SET state = ?, updated_at = ?, timestamps_json = ?, version = version + 1
                    WHERE task_id = ? AND version = ?
                    """,
                    (TaskState.EXPIRED.value, now, _json_dump(timestamps), task.task_id, task.version),
                )
                expired = self._task_for_update(connection, task.task_id)
                self._insert_event(
                    connection,
                    event_type="TASK_EXPIRED",
                    task=expired,
                    actor=request.actor,
                    approval_id=approval.approval_id,
                    metadata={"reason": "approval expired"},
                )
                return ApprovalConsumptionResult(
                    False, "approval has expired", ErrorCode.APPROVAL_EXPIRED
                )

            consumed = connection.execute(
                """
                UPDATE approvals SET consumed_at = ?, consumed_by_task_id = ?
                WHERE approval_id = ? AND consumed_at IS NULL
                """,
                (now, task.task_id, approval.approval_id),
            )
            if consumed.rowcount != 1:
                return ApprovalConsumptionResult(
                    False, "approval was consumed concurrently", ErrorCode.APPROVAL_ALREADY_CONSUMED
                )
            timestamps = dict(task.timestamps)
            timestamps[TaskState.RUNNING.value] = now
            claimed = connection.execute(
                """
                UPDATE tasks SET state = ?, updated_at = ?, timestamps_json = ?, version = version + 1
                WHERE task_id = ? AND version = ? AND state = ?
                """,
                (
                    TaskState.RUNNING.value,
                    now,
                    _json_dump(timestamps),
                    task.task_id,
                    task.version,
                    TaskState.APPROVED.value,
                ),
            )
            if claimed.rowcount != 1:
                raise TaskError(ErrorCode.CONCURRENT_MODIFICATION, "task execution was claimed concurrently")
            running = self._task_for_update(connection, task.task_id)
            self._insert_event(
                connection,
                event_type="APPROVAL_CONSUMED",
                task=running,
                actor=request.actor,
                approval_id=approval.approval_id,
                metadata={"request_digest": request.digest()},
            )
            self._insert_event(
                connection,
                event_type="TASK_STARTED",
                task=running,
                actor=request.actor,
                approval_id=approval.approval_id,
                metadata={"from_state": TaskState.APPROVED.value, "to_state": TaskState.RUNNING.value},
            )
        return ApprovalConsumptionResult(
            True,
            f"persistent approval {grant.grant_id} consumed for task {request.task_id}",
        )

    def recover_running(self, actor: Actor) -> tuple[PersistentTask, ...]:
        recovered_ids: list[str] = []
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks WHERE state = ? ORDER BY task_id", (TaskState.RUNNING.value,)
            ).fetchall()
            for row in rows:
                task = self._task_from_row(row)
                now = _utc_now()
                timestamps = dict(task.timestamps)
                timestamps[TaskState.RECOVERY_REQUIRED.value] = now
                connection.execute(
                    """
                    UPDATE tasks SET state = ?, updated_at = ?, timestamps_json = ?, version = version + 1
                    WHERE task_id = ? AND version = ? AND state = ?
                    """,
                    (
                        TaskState.RECOVERY_REQUIRED.value,
                        now,
                        _json_dump(timestamps),
                        task.task_id,
                        task.version,
                        TaskState.RUNNING.value,
                    ),
                )
                recovered = self._task_for_update(connection, task.task_id)
                self._insert_event(
                    connection,
                    event_type="TASK_RECOVERY_REQUIRED",
                    task=recovered,
                    actor=actor,
                    approval_id=recovered.approval_grant_id,
                    metadata={
                        "reason": "APOS restarted while task was recorded as RUNNING",
                        "automatic_execution_resumed": False,
                    },
                )
                recovered_ids.append(task.task_id)
        return tuple(self.get_task(task_id) for task_id in recovered_ids)

    def pending_events(self) -> tuple[TaskOutboxEvent, ...]:
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT * FROM task_events WHERE published_at IS NULL ORDER BY sequence"
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        return tuple(
            TaskOutboxEvent(
                event_id=row["event_id"],
                event_type=row["event_type"],
                task_id=row["task_id"],
                request_id=row["request_id"],
                approval_id=row["approval_id"],
                actor=Actor(ActorKind(row["actor_kind"]), row["actor_id"]),
                capability=Capability(row["capability"]),
                metadata=_json_load(row["metadata_json"], {}),
                created_at=row["created_at"],
            )
            for row in rows
        )

    def mark_event_published(self, event_id: str, audit_event_id: str) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE task_events SET published_at = ?, audit_event_id = ?
                WHERE event_id = ? AND published_at IS NULL
                """,
                (_utc_now(), audit_event_id, event_id),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _transaction(self):
        return _SQLiteTransaction(self)

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE permission_requests (
                request_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL UNIQUE,
                project_id TEXT NOT NULL,
                actor_kind TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                capability TEXT NOT NULL,
                resource TEXT NOT NULL,
                operation TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                request_digest TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );

            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                actor_kind TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                description TEXT NOT NULL,
                requested_capability TEXT NOT NULL,
                state TEXT NOT NULL,
                permission_request_id TEXT NOT NULL UNIQUE,
                approval_grant_id TEXT UNIQUE,
                retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
                timestamps_json TEXT NOT NULL,
                failure_json TEXT,
                cancellation_json TEXT,
                metadata_json TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
                FOREIGN KEY(permission_request_id) REFERENCES permission_requests(request_id)
            );

            CREATE TABLE approvals (
                approval_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL UNIQUE,
                request_id TEXT NOT NULL UNIQUE,
                project_id TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                subject_kind TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                approved_by_kind TEXT NOT NULL,
                approved_by_id TEXT NOT NULL,
                note TEXT NOT NULL,
                source TEXT NOT NULL,
                authenticated INTEGER NOT NULL CHECK (authenticated IN (0, 1)),
                issued_at TEXT NOT NULL,
                expires_at TEXT,
                consumed_at TEXT,
                consumed_by_task_id TEXT,
                FOREIGN KEY(task_id) REFERENCES tasks(task_id),
                FOREIGN KEY(request_id) REFERENCES permission_requests(request_id)
            );

            CREATE TABLE task_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                task_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                approval_id TEXT,
                actor_kind TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                capability TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                published_at TEXT,
                audit_event_id TEXT,
                FOREIGN KEY(task_id) REFERENCES tasks(task_id)
            );

            CREATE INDEX idx_tasks_state ON tasks(state);
            CREATE INDEX idx_task_events_pending ON task_events(published_at, sequence);
            """
        )

    def _task_for_update(self, connection: sqlite3.Connection, task_id: str) -> PersistentTask:
        row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise TaskError(ErrorCode.TASK_NOT_FOUND, f"task does not exist: {task_id}")
        return self._task_from_row(row)

    def _request_for_update(self, connection: sqlite3.Connection, request_id: str) -> PermissionRequest:
        row = connection.execute(
            "SELECT * FROM permission_requests WHERE request_id = ?", (request_id,)
        ).fetchone()
        if row is None:
            raise TaskError(ErrorCode.PERMISSION_DENIED, "persistent permission request does not exist")
        request = PermissionRequest(
            request_id=row["request_id"],
            project_id=row["project_id"],
            actor=Actor(ActorKind(row["actor_kind"]), row["actor_id"]),
            capability=Capability(row["capability"]),
            resource=row["resource"],
            operation=row["operation"],
            risk_level=RiskLevel[row["risk_level"]],
            metadata=_json_load(row["metadata_json"], {}),
            task_id=row["task_id"],
        )
        if request.digest() != row["request_digest"]:
            raise TaskError(ErrorCode.PERSISTENCE_CORRUPTED, "persistent request digest mismatch")
        return request

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> PersistentTask:
        return PersistentTask(
            task_id=row["task_id"],
            project_id=row["project_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            actor=Actor(ActorKind(row["actor_kind"]), row["actor_id"]),
            description=row["description"],
            requested_capability=Capability(row["requested_capability"]),
            state=TaskState(row["state"]),
            permission_request_id=row["permission_request_id"],
            approval_grant_id=row["approval_grant_id"],
            retry_count=int(row["retry_count"]),
            timestamps=_json_load(row["timestamps_json"], {}),
            failure_information=_json_load(row["failure_json"], None),
            cancellation_information=_json_load(row["cancellation_json"], None),
            metadata=_json_load(row["metadata_json"], {}),
            version=int(row["version"]),
        )

    @staticmethod
    def _approval_from_row(row: sqlite3.Row) -> PersistentApproval:
        return PersistentApproval(
            approval_id=row["approval_id"],
            task_id=row["task_id"],
            request_id=row["request_id"],
            project_id=row["project_id"],
            request_digest=row["request_digest"],
            subject=Actor(ActorKind(row["subject_kind"]), row["subject_id"]),
            approved_by=Actor(ActorKind(row["approved_by_kind"]), row["approved_by_id"]),
            note=row["note"],
            source=ApprovalSource(row["source"]),
            authenticated=bool(row["authenticated"]),
            issued_at=row["issued_at"],
            expires_at=row["expires_at"],
            consumed_at=row["consumed_at"],
            consumed_by_task_id=row["consumed_by_task_id"],
        )

    @staticmethod
    def _approval_mismatch(
        task: PersistentTask,
        stored_request: PermissionRequest,
        supplied_request: PermissionRequest,
        stored: PersistentApproval,
        supplied: ApprovalGrant,
    ) -> str | None:
        if supplied_request.digest() != stored_request.digest():
            return "permission request differs from the persisted canonical request"
        if supplied_request.task_id != task.task_id:
            return "permission request task does not match approval task"
        if supplied_request.actor != task.actor or stored.subject != task.actor:
            return "approval subject does not match task actor"
        if task.approval_grant_id != stored.approval_id:
            return "task does not reference this approval"
        if (
            supplied.request_id != stored.request_id
            or supplied.project_id != stored.project_id
            or supplied.request_digest != stored.request_digest
            or supplied.approved_by != stored.approved_by
            or supplied.grant_id != stored.approval_id
            or supplied.approval_source != stored.source
            or supplied.authenticated != stored.authenticated
            or supplied.expires_at != stored.expires_at
        ):
            return "approval grant differs from the persisted approval"
        return None

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        *,
        event_type: str,
        task: PersistentTask,
        actor: Actor,
        metadata: dict[str, Any],
        approval_id: str | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO task_events (
                event_id, event_type, task_id, request_id, approval_id, actor_kind,
                actor_id, capability, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid4().hex,
                event_type,
                task.task_id,
                task.permission_request_id,
                approval_id,
                actor.kind.value,
                actor.actor_id,
                task.requested_capability.value,
                _json_dump(metadata),
                _utc_now(),
            ),
        )

    @staticmethod
    def _database_error(exc: sqlite3.DatabaseError) -> TaskError:
        return TaskError(ErrorCode.PERSISTENCE_CORRUPTED, f"task store operation failed: {exc}")


class _SQLiteTransaction:
    def __init__(self, repository: SQLiteTaskRepository) -> None:
        self.repository = repository
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        try:
            self.connection = self.repository._connect()
            self.connection.execute("BEGIN IMMEDIATE")
            return self.connection
        except sqlite3.DatabaseError as exc:
            if self.connection is not None:
                self.connection.close()
            raise self.repository._database_error(exc) from exc

    def __exit__(self, exc_type, exc, traceback) -> bool:
        assert self.connection is not None
        try:
            if exc_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        except sqlite3.DatabaseError as database_exc:
            raise self.repository._database_error(database_exc) from database_exc
        finally:
            self.connection.close()
        return False


class TaskService:
    """Official persistent task lifecycle, approval, and controlled-execution boundary."""

    def __init__(
        self,
        workspace: ProjectWorkspace,
        repository: TaskRepository,
        audit_log: AuditLog,
        *,
        human_approval_boundary: HumanApprovalBoundary | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        if audit_log.workspace.project_id != workspace.project_id:
            raise ValueError("task audit log belongs to a different project")
        self.workspace = workspace
        self._repository = repository
        self.audit_log = audit_log
        self.redactor = redactor or audit_log.redactor
        self._execution_service: ControlledExecutionService | None = None
        self.human_approval_boundary = (
            human_approval_boundary or LocalUnauthenticatedHumanApprovalBoundary()
        )
        self._repository.initialize()

    def _bind_execution_service(self, execution_service: "ControlledExecutionService") -> None:
        """Bind the controlled executor once from the ProjectRuntime composition root."""

        if self._execution_service is not None:
            raise RuntimeError("controlled execution service is already bound")
        if execution_service.workspace.project_id != self.workspace.project_id:
            raise ValueError("execution service belongs to a different project")
        self._execution_service = execution_service

    def recover_interrupted_tasks(self) -> tuple[PersistentTask, ...]:
        """Recover RUNNING tasks at an explicit, dedicated task-owner startup boundary."""

        recovered = self._repository.recover_running(
            Actor(ActorKind.SYSTEM, "p0-3a-crash-recovery")
        )
        self.flush_audit_events()
        return recovered

    def create_task(
        self,
        permission_request: PermissionRequest,
        *,
        description: str,
        metadata: dict[str, Any] | None = None,
    ) -> PersistentTask:
        if permission_request.project_id != self.workspace.project_id:
            raise TaskError(ErrorCode.PERMISSION_DENIED, "permission request belongs to another project")
        if not permission_request.task_id:
            raise TaskError(
                ErrorCode.INVALID_ARGUMENT,
                "persistent tasks require permission_request.task_id",
            )
        self._ensure_request_has_no_secrets(permission_request)
        now = _utc_now()
        task = PersistentTask(
            task_id=permission_request.task_id,
            project_id=self.workspace.project_id,
            created_at=now,
            updated_at=now,
            actor=permission_request.actor,
            description=self.redactor.redact_text(description),
            requested_capability=permission_request.capability,
            state=TaskState.CREATED,
            permission_request_id=permission_request.request_id,
            timestamps={TaskState.CREATED.value: now},
            metadata=self.redactor.redact(metadata or {}),
        )
        created = self._repository.create(task, permission_request)
        self.flush_audit_events()
        return created

    def create_command_task(
        self,
        request: "CommandRequest",
        *,
        description: str,
        metadata: dict[str, Any] | None = None,
    ) -> PersistentTask:
        """Persist the canonical request prepared by the bound execution service."""

        if self._execution_service is None:
            raise TaskError(
                ErrorCode.INTERNAL_ERROR,
                "controlled execution service is not configured for this task service",
            )
        prepared = self._execution_service.prepare(request)
        return self.create_task(
            prepared.permission_request,
            description=description,
            metadata=metadata,
        )

    def get_task(self, task_id: str) -> PersistentTask:
        return self._repository.get_task(task_id)

    def get_permission_request(self, task_id: str) -> PermissionRequest:
        task = self._repository.get_task(task_id)
        return self._repository.get_permission_request(task.permission_request_id)

    def get_approval(self, task_id: str) -> PersistentApproval:
        task = self._repository.get_task(task_id)
        if task.approval_grant_id is None:
            raise TaskError(ErrorCode.APPROVAL_NOT_FOUND, "task has no approval")
        return self._repository.get_approval(task.approval_grant_id)

    def queue_task(self, task_id: str, *, actor: Actor) -> PersistentTask:
        task = self._require_subject(task_id, actor)
        if task.state != TaskState.CREATED:
            TaskStateMachine.validate(task.state, TaskState.QUEUED)
        changed = self._repository.transition(
            task_id,
            TaskState.QUEUED,
            actor=actor,
            event_types=("TASK_QUEUED",),
        )
        self.flush_audit_events()
        return changed

    def request_approval(self, task_id: str, *, actor: Actor) -> PersistentTask:
        self._require_subject(task_id, actor)
        changed = self._repository.transition(
            task_id,
            TaskState.WAITING_APPROVAL,
            actor=actor,
            event_types=("TASK_WAITING_APPROVAL", "APPROVAL_REQUESTED"),
        )
        self.flush_audit_events()
        return changed

    def grant_approval(
        self,
        task_id: str,
        *,
        action: ApprovalAction,
    ) -> PersistentApproval:
        task = self._repository.get_task(task_id)
        request = self._repository.get_permission_request(task.permission_request_id)
        if task.state != TaskState.WAITING_APPROVAL:
            TaskStateMachine.validate(task.state, TaskState.APPROVED)
        if action.request_id != request.request_id or action.request_digest != request.digest():
            raise TaskError(
                ErrorCode.PERMISSION_DENIED,
                "approval action does not match the persisted permission request",
            )
        if action.subject != task.actor or action.subject != request.actor:
            raise TaskError(
                ErrorCode.APPROVAL_SUBJECT_MISMATCH,
                "approval subject does not match task actor",
            )
        self.human_approval_boundary.validate(action)
        if action.expires_at is not None:
            _parse_time(action.expires_at)
        approval = PersistentApproval(
            approval_id=uuid4().hex,
            task_id=task.task_id,
            request_id=request.request_id,
            project_id=task.project_id,
            request_digest=request.digest(),
            subject=task.actor,
            approved_by=action.approved_by,
            note=self.redactor.redact_text(action.note),
            source=action.source,
            authenticated=False,
            issued_at=_utc_now(),
            expires_at=action.expires_at,
        )
        _, stored = self._repository.issue_approval(task_id, approval)
        self.flush_audit_events()
        return stored

    def consume_approval(
        self,
        request: PermissionRequest,
        approval: ApprovalGrant,
    ) -> ApprovalConsumptionResult:
        result = self._repository.consume_approval(request, approval)
        self.flush_audit_events()
        return result

    def requires_persistent_approval(self, request: PermissionRequest) -> bool:
        if request.task_id is None:
            return False
        try:
            self._repository.get_task(request.task_id)
        except TaskError as exc:
            if exc.code == ErrorCode.TASK_NOT_FOUND:
                return False
            raise
        return True

    def run_command_task(
        self,
        request: "CommandRequest",
        *,
        network_approval: ApprovalGrant | None = None,
    ):
        """Run through the runtime-bound controlled executor and close the task lifecycle."""

        if self._execution_service is None:
            raise TaskError(
                ErrorCode.INTERNAL_ERROR,
                "controlled execution service is not configured for this task service",
            )
        if request.task_id is None:
            raise TaskError(ErrorCode.INVALID_ARGUMENT, "command task_id is required")
        task = self._require_subject(request.task_id, request.actor)
        if task.state != TaskState.APPROVED:
            raise TaskError(
                ErrorCode.INVALID_TASK_TRANSITION,
                f"command task must be APPROVED, not {task.state.value}",
            )
        if task.permission_request_id != request.request_id:
            raise TaskError(
                ErrorCode.PERMISSION_DENIED,
                "command request does not match task permission request",
            )
        if task.approval_grant_id is None:
            raise TaskError(ErrorCode.APPROVAL_NOT_FOUND, "task has no approval")
        approval = self._repository.get_approval(task.approval_grant_id)
        result = self._execution_service.run(
            request,
            approval=approval.to_grant(),
            network_approval=network_approval,
        )
        current = self._repository.get_task(task.task_id)
        if current.state == TaskState.RUNNING:
            if result.success:
                self.complete_task(task.task_id, actor=task.actor, succeeded=True)
            elif result.error is not None and result.error.code == ErrorCode.EXECUTION_CANCELLED:
                self.cancel_task(
                    task.task_id,
                    actor=task.actor,
                    reason=result.error.message,
                    execution_stopped=True,
                )
            else:
                self.complete_task(
                    task.task_id,
                    actor=task.actor,
                    succeeded=False,
                    failure_information={
                        "error_code": result.error.code.value if result.error else ErrorCode.INTERNAL_ERROR.value,
                        "message": result.error.message if result.error else "execution failed",
                    },
                )
        return result

    def complete_task(
        self,
        task_id: str,
        *,
        actor: Actor,
        succeeded: bool,
        failure_information: dict[str, Any] | None = None,
    ) -> PersistentTask:
        self._require_subject(task_id, actor)
        target = TaskState.SUCCEEDED if succeeded else TaskState.FAILED
        event = "TASK_SUCCEEDED" if succeeded else "TASK_FAILED"
        changed = self._repository.transition(
            task_id,
            target,
            actor=actor,
            event_types=(event,),
            failure_information=(
                None if succeeded else self.redactor.redact(failure_information or {"reason": "failed"})
            ),
        )
        self.flush_audit_events()
        return changed

    def cancel_task(
        self,
        task_id: str,
        *,
        actor: Actor,
        reason: str,
        execution_stopped: bool = False,
    ) -> PersistentTask:
        task = self._require_subject(task_id, actor)
        if task.state == TaskState.RUNNING and not execution_stopped:
            raise TaskError(
                ErrorCode.INVALID_TASK_TRANSITION,
                "RUNNING task cannot be cancelled until execution is confirmed stopped",
            )
        changed = self._repository.transition(
            task_id,
            TaskState.CANCELLED,
            actor=actor,
            event_types=("TASK_CANCELLED",),
            cancellation_information={"reason": self.redactor.redact_text(reason)},
        )
        self.flush_audit_events()
        return changed

    def expire_task(self, task_id: str, *, actor: Actor, reason: str) -> PersistentTask:
        self._require_subject(task_id, actor)
        changed = self._repository.transition(
            task_id,
            TaskState.EXPIRED,
            actor=actor,
            event_types=("TASK_EXPIRED",),
            failure_information={"reason": self.redactor.redact_text(reason)},
        )
        self.flush_audit_events()
        return changed

    def resolve_recovery(
        self,
        task_id: str,
        *,
        actor: Actor,
        cancelled: bool,
        reason: str,
    ) -> PersistentTask:
        task = self._require_subject(task_id, actor)
        if task.state != TaskState.RECOVERY_REQUIRED:
            raise TaskError(
                ErrorCode.INVALID_TASK_TRANSITION,
                "only RECOVERY_REQUIRED tasks can be resolved",
            )
        target = TaskState.CANCELLED if cancelled else TaskState.FAILED
        event = "TASK_CANCELLED" if cancelled else "TASK_FAILED"
        changed = self._repository.transition(
            task_id,
            target,
            actor=actor,
            event_types=(event,),
            failure_information=(None if cancelled else {"reason": self.redactor.redact_text(reason)}),
            cancellation_information=(
                {"reason": self.redactor.redact_text(reason)} if cancelled else None
            ),
        )
        self.flush_audit_events()
        return changed

    def flush_audit_events(self) -> None:
        existing_ids = {event.get("event_id") for event in self.audit_log.events()}
        for event in self._repository.pending_events():
            if event.event_id in existing_ids:
                self._repository.mark_event_published(event.event_id, event.event_id)
                continue
            audit_event = self.audit_log.record(
                event_id=event.event_id,
                actor=event.actor.to_dict(),
                operation=(
                    "approval.lifecycle" if event.event_type.startswith("APPROVAL_") else "task.lifecycle"
                ),
                capability=event.capability.value,
                resource=f"task:{event.task_id}",
                status=_audit_status(event.event_type),
                request_id=event.request_id,
                metadata={
                    "event_type": event.event_type,
                    "task_event_id": event.event_id,
                    "approval_id": event.approval_id,
                    **event.metadata,
                },
                task_id=event.task_id,
            )
            self._repository.mark_event_published(event.event_id, audit_event.event_id)
            existing_ids.add(event.event_id)

    def _require_subject(self, task_id: str, actor: Actor) -> PersistentTask:
        task = self._repository.get_task(task_id)
        if actor != task.actor:
            raise TaskError(
                ErrorCode.APPROVAL_SUBJECT_MISMATCH,
                "actor does not match task subject",
            )
        return task

    def _ensure_request_has_no_secrets(self, request: PermissionRequest) -> None:
        redacted = self.redactor.redact(
            {
                "resource": request.resource,
                "operation": request.operation,
                "metadata": request.metadata,
            }
        )
        original = {
            "resource": request.resource,
            "operation": request.operation,
            "metadata": request.metadata,
        }
        if redacted != original:
            raise TaskError(
                ErrorCode.PERMISSION_DENIED,
                "permission request contains data classified as secret; persist a digest instead",
            )


def _audit_status(event_type: str) -> AuditStatus:
    if event_type in {"TASK_WAITING_APPROVAL", "APPROVAL_REQUESTED"}:
        return AuditStatus.APPROVAL_REQUIRED
    if event_type in {"APPROVAL_GRANTED", "TASK_APPROVED", "APPROVAL_CONSUMED"}:
        return AuditStatus.AUTHORIZED
    if event_type == "TASK_STARTED":
        return AuditStatus.STARTED
    if event_type == "TASK_SUCCEEDED":
        return AuditStatus.COMPLETED
    if event_type in {"TASK_FAILED", "TASK_RECOVERY_REQUIRED"}:
        return AuditStatus.FAILED
    if event_type in {"TASK_CANCELLED", "TASK_EXPIRED"}:
        return AuditStatus.CANCELLED
    return AuditStatus.COMPLETED


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TaskError(ErrorCode.INVALID_ARGUMENT, f"invalid ISO timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise TaskError(ErrorCode.INVALID_ARGUMENT, "timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _json_dump(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def _json_load(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise TaskError(ErrorCode.PERSISTENCE_CORRUPTED, "invalid JSON in task store") from exc
