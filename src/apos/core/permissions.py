from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
import hashlib
import json
import threading
from typing import Any, Mapping, Protocol
from uuid import uuid4

from .audit import AuditEvent, AuditLog, AuditStatus
from .result import ErrorCode


class Capability(str, Enum):
    PROJECT_READ = "PROJECT_READ"
    PROJECT_WRITE = "PROJECT_WRITE"
    PROJECT_DELETE = "PROJECT_DELETE"
    PROCESS_EXECUTE = "PROCESS_EXECUTE"
    GIT_READ = "GIT_READ"
    GIT_WRITE = "GIT_WRITE"
    GIT_RESET = "GIT_RESET"
    GIT_PUSH = "GIT_PUSH"
    TEST_EXECUTE = "TEST_EXECUTE"
    LOCAL_LLM_EXECUTE = "LOCAL_LLM_EXECUTE"
    NETWORK_ACCESS = "NETWORK_ACCESS"
    SECRET_ACCESS = "SECRET_ACCESS"


class ActorKind(str, Enum):
    USER = "USER"
    EXTERNAL_AI = "EXTERNAL_AI"
    LOCAL_LLM = "LOCAL_LLM"
    SYSTEM = "SYSTEM"


class RiskLevel(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class Decision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"


@dataclass(frozen=True)
class Actor:
    kind: ActorKind
    actor_id: str

    def __post_init__(self) -> None:
        if not self.actor_id.strip():
            raise ValueError("actor_id is required")

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value, "actor_id": self.actor_id}


@dataclass(frozen=True)
class PermissionRequest:
    request_id: str
    project_id: str
    actor: Actor
    capability: Capability
    resource: str
    operation: str
    risk_level: RiskLevel
    metadata: dict[str, Any] = field(default_factory=dict)
    task_id: str | None = None

    def digest(self) -> str:
        payload = {
            "request_id": self.request_id,
            "project_id": self.project_id,
            "actor": self.actor.to_dict(),
            "capability": self.capability.value,
            "resource": self.resource,
            "operation": self.operation,
            "risk_level": self.risk_level.name,
            "metadata": self.metadata,
            "task_id": self.task_id,
        }
        serialized = json.dumps(
            payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        actor: Actor,
        capability: Capability,
        resource: str,
        operation: str,
        risk_level: RiskLevel,
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
        task_id: str | None = None,
    ) -> "PermissionRequest":
        return cls(
            request_id=request_id or uuid4().hex,
            project_id=project_id,
            actor=actor,
            capability=capability,
            resource=resource,
            operation=operation,
            risk_level=risk_level,
            metadata=dict(metadata or {}),
            task_id=task_id,
        )


@dataclass(frozen=True)
class ApprovalGrant:
    request_id: str
    project_id: str
    request_digest: str
    approved_by: Actor
    note: str

    def __post_init__(self) -> None:
        if self.approved_by.kind not in {ActorKind.USER, ActorKind.SYSTEM}:
            raise ValueError("approval grants must be issued by a USER or SYSTEM actor")
        if not self.note.strip():
            raise ValueError("approval note is required")
        if len(self.request_digest) != 64:
            raise ValueError("approval request_digest must be a SHA-256 digest")


@dataclass(frozen=True)
class PermissionDecision:
    decision: Decision
    capability: Capability
    reason: str
    policy_id: str | None = None
    error_code: ErrorCode | None = None


class PermissionPolicy(Protocol):
    policy_id: str

    def evaluate(self, request: PermissionRequest) -> PermissionDecision:
        ...


@dataclass(frozen=True)
class StaticPermissionPolicy:
    """Explicit capability policy. Missing rules fail closed."""

    rules: Mapping[Capability, Decision]
    policy_id: str = "static-v1"
    default_decision: Decision = Decision.DENY

    def evaluate(self, request: PermissionRequest) -> PermissionDecision:
        decision = self.rules.get(request.capability, self.default_decision)
        if decision == Decision.ALLOW:
            reason = f"{request.capability.value} is explicitly allowed"
        elif decision == Decision.APPROVAL_REQUIRED:
            reason = f"{request.capability.value} requires trusted approval"
        else:
            reason = f"{request.capability.value} is not allowed by policy"
        return PermissionDecision(decision, request.capability, reason, self.policy_id)


class PermissionEngine:
    """The sole authority for capability decisions in the new APOS core."""

    def __init__(self, policy: PermissionPolicy) -> None:
        self.policy = policy
        self._consumed_approvals: set[tuple[str, str]] = set()
        self._approval_lock = threading.Lock()

    def evaluate(
        self,
        request: PermissionRequest,
        *,
        approval: ApprovalGrant | None = None,
    ) -> PermissionDecision:
        try:
            decision = self.policy.evaluate(request)
        except Exception as exc:
            return PermissionDecision(
                Decision.DENY,
                request.capability,
                f"policy evaluation failed: {type(exc).__name__}",
                getattr(self.policy, "policy_id", None),
                ErrorCode.POLICY_EVALUATION_FAILED,
            )

        if decision.capability != request.capability:
            return PermissionDecision(
                Decision.DENY,
                request.capability,
                "policy returned a decision for the wrong capability",
                decision.policy_id,
                ErrorCode.POLICY_EVALUATION_FAILED,
            )
        if decision.decision != Decision.APPROVAL_REQUIRED or approval is None:
            return decision
        if (
            approval.request_id != request.request_id
            or approval.project_id != request.project_id
            or approval.request_digest != request.digest()
        ):
            return PermissionDecision(
                Decision.DENY,
                request.capability,
                "approval grant does not match this request",
                decision.policy_id,
                ErrorCode.PERMISSION_DENIED,
            )
        approval_key = (approval.project_id, approval.request_id)
        with self._approval_lock:
            if approval_key in self._consumed_approvals:
                return PermissionDecision(
                    Decision.DENY,
                    request.capability,
                    "approval grant was already consumed",
                    decision.policy_id,
                    ErrorCode.PERMISSION_DENIED,
                )
            self._consumed_approvals.add(approval_key)
        return PermissionDecision(
            Decision.ALLOW,
            request.capability,
            f"approved by {approval.approved_by.kind.value}:{approval.approved_by.actor_id}",
            decision.policy_id,
        )


@dataclass(frozen=True)
class AuthorizationRecord:
    request: PermissionRequest
    decision: PermissionDecision
    requested_event: AuditEvent
    decision_event: AuditEvent

    @property
    def allowed(self) -> bool:
        return self.decision.decision == Decision.ALLOW


class AuthorizationService:
    """Central permission and audit gateway for privileged operations."""

    def __init__(self, engine: PermissionEngine, audit_log: AuditLog) -> None:
        self.engine = engine
        self.audit_log = audit_log

    def authorize(
        self,
        *,
        actor: Actor,
        capability: Capability,
        resource: str,
        operation: str,
        risk_level: RiskLevel,
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
        task_id: str | None = None,
        approval: ApprovalGrant | None = None,
    ) -> AuthorizationRecord:
        request = PermissionRequest.create(
            project_id=self.audit_log.workspace.project_id,
            actor=actor,
            capability=capability,
            resource=resource,
            operation=operation,
            risk_level=risk_level,
            metadata=metadata,
            request_id=request_id,
            task_id=task_id,
        )
        requested_event = self.audit_log.record(
            actor=actor.to_dict(),
            operation=operation,
            capability=capability.value,
            resource=resource,
            status=AuditStatus.REQUESTED,
            request_id=request.request_id,
            metadata={"risk_level": risk_level.name, **dict(metadata or {})},
            task_id=task_id,
        )
        decision = self.engine.evaluate(request, approval=approval)
        status = {
            Decision.ALLOW: AuditStatus.AUTHORIZED,
            Decision.DENY: AuditStatus.DENIED,
            Decision.APPROVAL_REQUIRED: AuditStatus.APPROVAL_REQUIRED,
        }[decision.decision]
        decision_event = self.audit_log.record(
            actor=actor.to_dict(),
            operation=operation,
            capability=capability.value,
            resource=resource,
            status=status,
            request_id=request.request_id,
            decision=decision.decision.value,
            error_code=decision.error_code.value if decision.error_code else None,
            metadata={"reason": decision.reason, "policy_id": decision.policy_id},
            task_id=task_id,
            parent_event_id=requested_event.event_id,
        )
        return AuthorizationRecord(request, decision, requested_event, decision_event)

    def reject_before_authorization(
        self,
        *,
        actor: Actor,
        capability: Capability,
        resource: str,
        operation: str,
        risk_level: RiskLevel,
        error_code: ErrorCode,
        reason: str,
        request_id: str | None = None,
        task_id: str | None = None,
    ) -> AuthorizationRecord:
        request = PermissionRequest.create(
            project_id=self.audit_log.workspace.project_id,
            actor=actor,
            capability=capability,
            resource=resource,
            operation=operation,
            risk_level=risk_level,
            request_id=request_id,
            task_id=task_id,
        )
        requested_event = self.audit_log.record(
            actor=actor.to_dict(),
            operation=operation,
            capability=capability.value,
            resource=resource,
            status=AuditStatus.REQUESTED,
            request_id=request.request_id,
            metadata={"risk_level": risk_level.name},
            task_id=task_id,
        )
        decision = PermissionDecision(Decision.DENY, capability, reason, "project-boundary", error_code)
        decision_event = self.audit_log.record(
            actor=actor.to_dict(),
            operation=operation,
            capability=capability.value,
            resource=resource,
            status=AuditStatus.DENIED,
            request_id=request.request_id,
            decision=Decision.DENY.value,
            error_code=error_code.value,
            metadata={"reason": reason, "policy_id": "project-boundary"},
            task_id=task_id,
            parent_event_id=requested_event.event_id,
        )
        return AuthorizationRecord(request, decision, requested_event, decision_event)

    def record_started(self, record: AuthorizationRecord, *, metadata: dict[str, Any] | None = None) -> AuditEvent:
        return self.audit_log.record(
            actor=record.request.actor.to_dict(),
            operation=record.request.operation,
            capability=record.request.capability.value,
            resource=record.request.resource,
            status=AuditStatus.STARTED,
            request_id=record.request.request_id,
            decision=record.decision.decision.value,
            metadata=metadata,
            task_id=record.request.task_id,
            parent_event_id=record.decision_event.event_id,
        )

    def record_finished(
        self,
        record: AuthorizationRecord,
        started_event: AuditEvent,
        *,
        status: AuditStatus,
        duration_seconds: float,
        exit_code: int | None = None,
        error_code: ErrorCode | None = None,
        changed_paths: tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
    ) -> AuditEvent:
        if status not in {AuditStatus.COMPLETED, AuditStatus.FAILED, AuditStatus.CANCELLED}:
            raise ValueError("finished audit status must be COMPLETED, FAILED, or CANCELLED")
        return self.audit_log.record(
            actor=record.request.actor.to_dict(),
            operation=record.request.operation,
            capability=record.request.capability.value,
            resource=record.request.resource,
            status=status,
            request_id=record.request.request_id,
            decision=record.decision.decision.value,
            duration_seconds=duration_seconds,
            exit_code=exit_code,
            error_code=error_code.value if error_code else None,
            changed_paths=changed_paths,
            metadata=metadata,
            task_id=record.request.task_id,
            parent_event_id=started_event.event_id,
        )
