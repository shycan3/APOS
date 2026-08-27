from __future__ import annotations

import os
import hashlib
from pathlib import Path
from time import perf_counter
import stat
import tempfile
from typing import Any

from .audit import AuditEvent, AuditStatus
from .permissions import (
    Actor,
    ApprovalGrant,
    AuthorizationRecord,
    AuthorizationService,
    Capability,
    Decision,
    RiskLevel,
)
from .result import ErrorCode, ToolResult
from .workspace import ProjectWorkspace, WorkspaceViolation


class FileSystemService:
    """Authorized and audited project-bound file capabilities."""

    def __init__(
        self,
        workspace: ProjectWorkspace,
        authorization: AuthorizationService,
        *,
        max_read_bytes: int = 1_000_000,
    ) -> None:
        if max_read_bytes < 1:
            raise ValueError("max_read_bytes must be positive")
        if authorization.audit_log.workspace.project_id != workspace.project_id:
            raise ValueError("authorization service belongs to a different project")
        self.workspace = workspace
        self.authorization = authorization
        self.max_read_bytes = max_read_bytes

    def list_files(
        self,
        path: str = "",
        *,
        actor: Actor,
        recursive: bool = False,
        request_id: str | None = None,
        task_id: str | None = None,
        approval: ApprovalGrant | None = None,
    ) -> ToolResult[dict[str, Any]]:
        operation = "filesystem.list"
        try:
            normalized, directory = self.workspace.resolve(path, allow_root=True, must_exist=True)
        except WorkspaceViolation as exc:
            return self._boundary_failure(exc, actor, Capability.PROJECT_READ, operation, request_id, task_id)
        if not directory.is_dir():
            return self._rejected_failure(
                ErrorCode.PATH_NOT_DIRECTORY, "requested path is not a directory", normalized,
                actor, Capability.PROJECT_READ, operation, RiskLevel.LOW, request_id, task_id,
            )

        authorization = self.authorization.authorize(
            actor=actor,
            capability=Capability.PROJECT_READ,
            resource=normalized or ".",
            operation=operation,
            risk_level=RiskLevel.LOW,
            metadata={"recursive": recursive},
            request_id=request_id,
            task_id=task_id,
            approval=approval,
        )
        denied = self._authorization_failure(authorization)
        if denied:
            return denied

        started_at = perf_counter()
        started = self.authorization.record_started(authorization)
        try:
            entries: list[dict[str, Any]] = []
            self._collect_entries(directory, entries, recursive=recursive)
            entries.sort(key=lambda item: str(item["path"]))
            finished = self.authorization.record_finished(
                authorization, started, status=AuditStatus.COMPLETED,
                duration_seconds=perf_counter() - started_at, metadata={"entry_count": len(entries)},
            )
            return ToolResult.ok(
                {"path": normalized, "recursive": recursive, "entries": entries},
                meta=self._meta(authorization, finished),
            )
        except OSError as exc:
            return self._runtime_failure(authorization, started, started_at, ErrorCode.IO_ERROR, str(exc), path)

    def read_file(
        self,
        path: str,
        *,
        actor: Actor,
        encoding: str = "utf-8",
        request_id: str | None = None,
        task_id: str | None = None,
        approval: ApprovalGrant | None = None,
    ) -> ToolResult[dict[str, Any]]:
        operation = "filesystem.read"
        try:
            normalized, target = self.workspace.resolve(path, must_exist=True)
        except WorkspaceViolation as exc:
            return self._boundary_failure(exc, actor, Capability.PROJECT_READ, operation, request_id, task_id)
        if not target.is_file():
            return self._rejected_failure(
                ErrorCode.PATH_NOT_FILE, "requested path is not a file", normalized,
                actor, Capability.PROJECT_READ, operation, RiskLevel.LOW, request_id, task_id,
            )

        authorization = self.authorization.authorize(
            actor=actor,
            capability=Capability.PROJECT_READ,
            resource=normalized,
            operation=operation,
            risk_level=RiskLevel.LOW,
            metadata={"encoding": encoding},
            request_id=request_id,
            task_id=task_id,
            approval=approval,
        )
        denied = self._authorization_failure(authorization)
        if denied:
            return denied

        started_at = perf_counter()
        started = self.authorization.record_started(authorization)
        try:
            size = target.stat().st_size
            if size > self.max_read_bytes:
                return self._runtime_failure(
                    authorization, started, started_at, ErrorCode.FILE_TOO_LARGE,
                    f"file exceeds the {self.max_read_bytes}-byte read limit", normalized,
                    extra={"size_bytes": size, "limit_bytes": self.max_read_bytes},
                )
            try:
                content = target.read_text(encoding=encoding)
            except UnicodeDecodeError as exc:
                return self._runtime_failure(
                    authorization, started, started_at, ErrorCode.DECODE_FAILED, str(exc), normalized
                )
            finished = self.authorization.record_finished(
                authorization, started, status=AuditStatus.COMPLETED,
                duration_seconds=perf_counter() - started_at,
                metadata={"size_bytes": size, "encoding": encoding},
            )
            return ToolResult.ok(
                {"path": normalized, "content": content, "size_bytes": size, "encoding": encoding},
                meta=self._meta(authorization, finished),
            )
        except OSError as exc:
            return self._runtime_failure(authorization, started, started_at, ErrorCode.IO_ERROR, str(exc), path)

    def write_file(
        self,
        path: str,
        content: str,
        *,
        actor: Actor,
        encoding: str = "utf-8",
        create_parents: bool = False,
        request_id: str | None = None,
        task_id: str | None = None,
        approval: ApprovalGrant | None = None,
    ) -> ToolResult[dict[str, Any]]:
        operation = "filesystem.write"
        if not isinstance(content, str):
            return self._rejected_failure(
                ErrorCode.INVALID_ARGUMENT, "content must be a string", path,
                actor, Capability.PROJECT_WRITE, operation, RiskLevel.MEDIUM, request_id, task_id,
            )
        try:
            normalized, target = self.workspace.resolve(path)
        except WorkspaceViolation as exc:
            return self._boundary_failure(exc, actor, Capability.PROJECT_WRITE, operation, request_id, task_id)
        if target.exists() and not target.is_file():
            return self._rejected_failure(
                ErrorCode.PATH_NOT_FILE, "requested path is not a file", normalized,
                actor, Capability.PROJECT_WRITE, operation, RiskLevel.MEDIUM, request_id, task_id,
            )

        authorization = self.authorization.authorize(
            actor=actor,
            capability=Capability.PROJECT_WRITE,
            resource=normalized,
            operation=operation,
            risk_level=RiskLevel.MEDIUM,
            metadata={
                "encoding": encoding,
                "create_parents": create_parents,
                "content_size_bytes": len(content.encode(encoding, errors="replace")),
                "content_digest": hashlib.sha256(content.encode(encoding, errors="replace")).hexdigest(),
            },
            request_id=request_id,
            task_id=task_id,
            approval=approval,
        )
        denied = self._authorization_failure(authorization)
        if denied:
            return denied

        started_at = perf_counter()
        started = self.authorization.record_started(authorization)
        try:
            if create_parents:
                target.parent.mkdir(parents=True, exist_ok=True)
            elif not target.parent.is_dir():
                return self._runtime_failure(
                    authorization, started, started_at, ErrorCode.PATH_NOT_FOUND,
                    "parent directory does not exist", normalized,
                )
            self._atomic_write(target, content, encoding)
            size = len(content.encode(encoding))
            finished = self.authorization.record_finished(
                authorization, started, status=AuditStatus.COMPLETED,
                duration_seconds=perf_counter() - started_at, changed_paths=(normalized,),
                metadata={"size_bytes": size, "encoding": encoding, "atomic": True},
            )
            return ToolResult.ok(
                {"path": normalized, "size_bytes": size, "encoding": encoding, "atomic": True},
                meta=self._meta(authorization, finished),
            )
        except (LookupError, OSError, UnicodeError) as exc:
            return self._runtime_failure(authorization, started, started_at, ErrorCode.IO_ERROR, str(exc), path)

    def _collect_entries(self, directory: Path, entries: list[dict[str, Any]], *, recursive: bool) -> None:
        for entry in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            relative = entry.relative_to(self.workspace.root).as_posix()
            if self.workspace.secret_policy.denial_reason(relative):
                continue
            try:
                canonical_relative = entry.resolve(strict=False).relative_to(self.workspace.root).as_posix()
            except ValueError:
                entries.append({"path": relative, "type": "link", "accessible": False})
                continue
            if self.workspace.secret_policy.denial_reason(canonical_relative):
                continue
            if entry.is_symlink():
                entries.append({"path": relative, "type": "symlink", "accessible": True})
                continue
            if entry.is_dir():
                entries.append({"path": relative, "type": "directory"})
                if recursive:
                    self._collect_entries(entry, entries, recursive=True)
            elif entry.is_file():
                entries.append({"path": relative, "type": "file", "size_bytes": entry.stat().st_size})
            else:
                entries.append({"path": relative, "type": "other"})

    @staticmethod
    def _atomic_write(target: Path, content: str, encoding: str) -> None:
        existing_mode = target.stat().st_mode if target.exists() else None
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding=encoding, newline="", dir=target.parent,
                prefix=f".{target.name}.apos-", suffix=".tmp", delete=False,
            ) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
                temporary_path = Path(stream.name)
            if existing_mode is not None:
                os.chmod(temporary_path, stat.S_IMODE(existing_mode))
            os.replace(temporary_path, target)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _boundary_failure(
        self, exc: WorkspaceViolation, actor: Actor, capability: Capability, operation: str,
        request_id: str | None, task_id: str | None,
    ) -> ToolResult[Any]:
        risk = RiskLevel.LOW if capability == Capability.PROJECT_READ else RiskLevel.MEDIUM
        return self._rejected_failure(
            exc.code, str(exc), exc.path, actor, capability, operation, risk, request_id, task_id
        )

    def _rejected_failure(
        self, code: ErrorCode, message: str, path: str, actor: Actor, capability: Capability,
        operation: str, risk: RiskLevel, request_id: str | None, task_id: str | None,
    ) -> ToolResult[Any]:
        record = self.authorization.reject_before_authorization(
            actor=actor, capability=capability, resource=path, operation=operation,
            risk_level=risk, error_code=code, reason=message,
            request_id=request_id, task_id=task_id,
        )
        return ToolResult.fail(
            code, message, details={"path": path}, meta=self._meta(record, record.decision_event)
        )

    def _authorization_failure(self, record: AuthorizationRecord) -> ToolResult[Any] | None:
        if record.allowed:
            return None
        required = record.decision.decision == Decision.APPROVAL_REQUIRED
        code = record.decision.error_code or (
            ErrorCode.PERMISSION_REQUIRED if required else ErrorCode.PERMISSION_DENIED
        )
        return ToolResult.fail(
            code, record.decision.reason,
            details={"capability": record.request.capability.value, "resource": record.request.resource},
            meta=self._meta(record, record.decision_event),
        )

    def _runtime_failure(
        self, record: AuthorizationRecord, started: AuditEvent, started_at: float,
        code: ErrorCode, message: str, path: str, *, extra: dict[str, Any] | None = None,
    ) -> ToolResult[Any]:
        finished = self.authorization.record_finished(
            record, started, status=AuditStatus.FAILED,
            duration_seconds=perf_counter() - started_at, error_code=code,
            metadata={"message": message},
        )
        details = {"path": path}
        details.update(extra or {})
        return ToolResult.fail(code, message, details=details, meta=self._meta(record, finished))

    def _meta(self, record: AuthorizationRecord, event: AuditEvent) -> dict[str, str]:
        return {
            **self.workspace.result_meta(),
            "request_id": record.request.request_id,
            "permission_request_digest": record.request.digest(),
            "audit_event_id": event.event_id,
        }
