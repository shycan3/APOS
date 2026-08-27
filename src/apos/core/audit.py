from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Iterable
from uuid import uuid4

from .workspace import ProjectWorkspace


REDACTED = "[REDACTED]"


class AuditStatus(str, Enum):
    REQUESTED = "REQUESTED"
    AUTHORIZED = "AUTHORIZED"
    DENIED = "DENIED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Redactor:
    """Redacts common credential shapes from structured data and process output."""

    _SENSITIVE_KEY = re.compile(
        r"(?:password|passwd|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key)",
        re.IGNORECASE,
    )
    _INLINE_SECRET = re.compile(
        r"(?i)(--?(?:password|passwd|secret|token|api[_-]?key)(?:=|\s+))([^\s]+)"
    )
    _BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+\-/]+=*")
    _ASSIGNMENT = re.compile(
        r"(?im)\b([A-Z0-9_]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|PRIVATE_KEY)[A-Z0-9_]*)=([^\s]+)"
    )
    _PRIVATE_KEY_BLOCK = re.compile(
        r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
        re.IGNORECASE | re.DOTALL,
    )

    def __init__(self, secret_values: Iterable[str] = ()) -> None:
        self._secret_values = tuple(sorted({value for value in secret_values if value}, key=len, reverse=True))

    def add_secret_values(self, secret_values: Iterable[str]) -> None:
        combined = {*self._secret_values, *(value for value in secret_values if value)}
        self._secret_values = tuple(sorted(combined, key=len, reverse=True))

    def redact_text(self, value: str) -> str:
        redacted = value
        for secret in self._secret_values:
            redacted = redacted.replace(secret, REDACTED)
        redacted = self._INLINE_SECRET.sub(lambda match: f"{match.group(1)}{REDACTED}", redacted)
        redacted = self._BEARER.sub(lambda match: f"{match.group(1)}{REDACTED}", redacted)
        redacted = self._ASSIGNMENT.sub(lambda match: f"{match.group(1)}={REDACTED}", redacted)
        redacted = self._PRIVATE_KEY_BLOCK.sub(REDACTED, redacted)
        return redacted

    def redact(self, value: Any, *, key: str = "") -> Any:
        if key and self._SENSITIVE_KEY.search(key):
            return REDACTED
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, dict):
            return {str(item_key): self.redact(item, key=str(item_key)) for item_key, item in value.items()}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [self.redact(item) for item in value]
        return value


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    timestamp: str
    project_id: str
    actor: dict[str, str]
    operation: str
    capability: str
    resource: str
    status: AuditStatus
    request_id: str
    decision: str | None = None
    duration_seconds: float | None = None
    exit_code: int | None = None
    error_code: str | None = None
    changed_paths: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    task_id: str | None = None
    parent_event_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "project_id": self.project_id,
            "actor": dict(self.actor),
            "operation": self.operation,
            "capability": self.capability,
            "resource": self.resource,
            "decision": self.decision,
            "status": self.status.value,
            "duration_seconds": self.duration_seconds,
            "exit_code": self.exit_code,
            "error_code": self.error_code,
            "changed_paths": list(self.changed_paths),
            "metadata": dict(self.metadata),
            "task_id": self.task_id,
            "request_id": self.request_id,
            "parent_event_id": self.parent_event_id,
        }


class AuditLog:
    """Project-scoped append-only JSONL security event store."""

    def __init__(self, workspace: ProjectWorkspace, *, redactor: Redactor | None = None) -> None:
        self.workspace = workspace
        self.path = workspace.root / ".apos" / "audit" / "events.jsonl"
        self.redactor = redactor or Redactor()
        self._lock = threading.Lock()

    def record(
        self,
        *,
        actor: dict[str, str],
        operation: str,
        capability: str,
        resource: str,
        status: AuditStatus,
        request_id: str,
        decision: str | None = None,
        duration_seconds: float | None = None,
        exit_code: int | None = None,
        error_code: str | None = None,
        changed_paths: Iterable[str] = (),
        metadata: dict[str, Any] | None = None,
        task_id: str | None = None,
        parent_event_id: str | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_id=uuid4().hex,
            timestamp=datetime.now(timezone.utc).isoformat(),
            project_id=self.workspace.project_id,
            actor=self.redactor.redact(actor),
            operation=operation,
            capability=capability,
            resource=self.redactor.redact_text(resource),
            status=status,
            request_id=request_id,
            decision=decision,
            duration_seconds=duration_seconds,
            exit_code=exit_code,
            error_code=error_code,
            changed_paths=tuple(self.redactor.redact_text(path) for path in changed_paths),
            metadata=self.redactor.redact(metadata or {}),
            task_id=task_id,
            parent_event_id=parent_event_id,
        )
        serialized = json.dumps(event.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(serialized + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        return event

    def events(self, *, request_id: str | None = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self._lock:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        events = [json.loads(line) for line in lines if line.strip()]
        if request_id is not None:
            events = [event for event in events if event.get("request_id") == request_id]
        return events
