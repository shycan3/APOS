from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
from time import perf_counter
from typing import Any, BinaryIO, Mapping
from uuid import uuid4

from .audit import AuditEvent, AuditStatus, Redactor
from .permissions import (
    Actor,
    ApprovalGrant,
    AuthorizationRecord,
    AuthorizationService,
    Capability,
    Decision,
    PermissionRequest,
    RiskLevel,
)
from .result import ErrorCode, ToolResult
from .workspace import ProjectWorkspace, WorkspaceViolation


class NetworkPolicy(str, Enum):
    DENIED = "NETWORK_DENIED"
    ALLOWED = "NETWORK_ALLOWED"
    APPROVAL_REQUIRED = "NETWORK_APPROVAL_REQUIRED"


@dataclass(frozen=True)
class ResourceLimits:
    timeout_seconds: float = 120.0
    max_output_bytes_per_stream: int = 256_000
    memory_limit_bytes: int | None = None
    process_count_limit: int | None = None

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_output_bytes_per_stream < 1:
            raise ValueError("max_output_bytes_per_stream must be positive")


@dataclass(frozen=True)
class CommandRequest:
    executable: str
    actor: Actor
    args: tuple[str, ...] = ()
    cwd: str = ""
    environment: Mapping[str, str] = field(default_factory=dict)
    network_policy: NetworkPolicy = NetworkPolicy.DENIED
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    capability: Capability = Capability.PROCESS_EXECUTE
    request_id: str = field(default_factory=lambda: uuid4().hex)
    task_id: str | None = None

    def __post_init__(self) -> None:
        if not self.executable.strip():
            raise ValueError("executable is required")
        if self.capability not in {Capability.PROCESS_EXECUTE, Capability.TEST_EXECUTE}:
            raise ValueError("command capability must be PROCESS_EXECUTE or TEST_EXECUTE")
        if not self.request_id.strip():
            raise ValueError("request_id is required")
        if not all(isinstance(argument, str) for argument in self.args):
            raise ValueError("command args must be strings")


@dataclass(frozen=True)
class CommandAssessment:
    executable: Path | None
    risk_level: RiskLevel
    allowed: bool
    reason: str
    error_code: ErrorCode | None = None


@dataclass(frozen=True)
class PreparedCommand:
    """Canonical command assessment and permission request used by task execution."""

    operation: str
    normalized_cwd: str
    cwd: Path
    assessment: CommandAssessment
    permission_request: PermissionRequest


class CommandPreparationError(ValueError):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        resource: str,
        risk_level: RiskLevel,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.resource = resource
        self.risk_level = risk_level


class CommandPolicy:
    """Resolves only explicitly trusted executables without consulting project PATH."""

    _SHELL_META = re.compile(r"[;&|<>`\r\n\x00]")
    _NETWORK_TOOLS = frozenset({"curl", "wget", "pip", "npm", "npx", "git"})

    def __init__(self, trusted_executables: tuple[Path, ...]) -> None:
        resolved: dict[str, Path] = {}
        for executable in trusted_executables:
            candidate = executable.expanduser().resolve(strict=True)
            if not candidate.is_file():
                raise ValueError(f"trusted executable is not a file: {candidate}")
            resolved[os.path.normcase(str(candidate))] = candidate
        if not resolved:
            raise ValueError("at least one trusted executable is required")
        self._trusted = resolved
        self._safe_path = os.pathsep.join(sorted({str(path.parent) for path in resolved.values()}))

    @classmethod
    def current_python(cls) -> "CommandPolicy":
        return cls((Path(sys.executable),))

    def assess(self, request: CommandRequest) -> CommandAssessment:
        raw = request.executable.strip()
        if self._SHELL_META.search(raw):
            return CommandAssessment(None, RiskLevel.CRITICAL, False, "shell syntax is not an executable", ErrorCode.COMMAND_NOT_ALLOWED)
        if "/" in raw or "\\" in raw:
            path = Path(raw)
            if not path.is_absolute():
                return CommandAssessment(None, RiskLevel.HIGH, False, "project-relative executables are not trusted", ErrorCode.COMMAND_NOT_ALLOWED)
            try:
                candidate = path.resolve(strict=True)
            except OSError:
                return CommandAssessment(None, RiskLevel.HIGH, False, "executable does not exist", ErrorCode.COMMAND_NOT_ALLOWED)
        else:
            found = shutil.which(raw, path=self._safe_path)
            if not found:
                return CommandAssessment(None, RiskLevel.HIGH, False, "executable is not in the trusted command set", ErrorCode.COMMAND_NOT_ALLOWED)
            candidate = Path(found).resolve(strict=True)

        if os.path.normcase(str(candidate)) not in self._trusted:
            return CommandAssessment(candidate, RiskLevel.HIGH, False, "resolved executable is not trusted", ErrorCode.COMMAND_NOT_ALLOWED)

        basename = candidate.stem.casefold()
        arguments = [argument.casefold() for argument in request.args]
        python_package_install = "pip" in arguments and "install" in arguments
        node_package_install = basename in {"npm", "npx"} and any(
            argument in {"install", "add", "update"} for argument in arguments
        )
        explicit_network = basename in self._NETWORK_TOOLS or python_package_install or node_package_install or any(
            value.startswith(("http://", "https://")) for value in arguments
        )
        if explicit_network and request.network_policy == NetworkPolicy.DENIED:
            return CommandAssessment(candidate, RiskLevel.HIGH, False, "command explicitly requests network access", ErrorCode.NETWORK_ACCESS_DENIED)

        risk = RiskLevel.MEDIUM if request.capability == Capability.TEST_EXECUTE else RiskLevel.HIGH
        return CommandAssessment(candidate, risk, True, "executable is explicitly trusted")


class EnvironmentSanitizer:
    _NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    _SENSITIVE = re.compile(
        r"(?:password|passwd|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key)",
        re.IGNORECASE,
    )
    _BLOCKED = frozenset(
        {
            "PATH", "PYTHONPATH", "PYTHONHOME", "LD_PRELOAD", "LD_LIBRARY_PATH",
            "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH", "COMSPEC", "PATHEXT",
        }
    )
    _SAFE_HOST = ("SystemRoot", "WINDIR", "OS", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS")

    def secret_values(self, requested: Mapping[str, str]) -> tuple[str, ...]:
        inherited = tuple(
            value for name, value in os.environ.items() if self._SENSITIVE.search(name) and value
        )
        return (*inherited, *(value for value in requested.values() if value))

    def build(self, workspace: ProjectWorkspace, executable: Path, requested: Mapping[str, str]) -> tuple[dict[str, str], Redactor]:
        temporary = workspace.root / ".apos" / "tmp"
        temporary.mkdir(parents=True, exist_ok=True)
        environment: dict[str, str] = {
            "PATH": str(executable.parent),
            "TEMP": str(temporary),
            "TMP": str(temporary),
            "APOS_PROJECT_ROOT": str(workspace.root),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        }
        for name in self._SAFE_HOST:
            value = os.environ.get(name)
            if value:
                environment[name] = value
        if os.name == "nt":
            comspec = os.environ.get("ComSpec") or os.environ.get("COMSPEC")
            pathext = os.environ.get("PATHEXT")
            if comspec:
                environment["COMSPEC"] = comspec
            if pathext:
                environment["PATHEXT"] = pathext

        for name, value in requested.items():
            if not isinstance(name, str) or not isinstance(value, str) or not self._NAME.fullmatch(name):
                raise ValueError("environment overrides must be string name/value pairs")
            if name.upper() in self._BLOCKED or self._SENSITIVE.search(name):
                raise ValueError(f"environment variable is not allowed: {name}")
            environment[name] = value

        return environment, Redactor(self.secret_values(requested))


class _BoundedBuffer:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.data = bytearray()
        self.truncated = False

    def consume(self, stream: BinaryIO) -> None:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            remaining = self.limit - len(self.data)
            if remaining > 0:
                self.data.extend(chunk[:remaining])
            if len(chunk) > remaining:
                self.truncated = True

    def text(self) -> str:
        return bytes(self.data).decode("utf-8", errors="replace")


class ControlledExecutionService:
    """Authorized, audited, bounded process execution with no shell interpretation."""

    def __init__(
        self,
        workspace: ProjectWorkspace,
        authorization: AuthorizationService,
        command_policy: CommandPolicy,
        *,
        environment_sanitizer: EnvironmentSanitizer | None = None,
    ) -> None:
        if authorization.audit_log.workspace.project_id != workspace.project_id:
            raise ValueError("authorization service belongs to a different project")
        self.workspace = workspace
        self.authorization = authorization
        self.command_policy = command_policy
        self.environment_sanitizer = environment_sanitizer or EnvironmentSanitizer()
        self._active: dict[str, subprocess.Popen[bytes]] = {}
        self._cancelled: set[str] = set()
        self._active_lock = threading.Lock()
        self._project_lock = threading.Lock()

    def active_request_ids(self) -> tuple[str, ...]:
        with self._active_lock:
            return tuple(sorted(self._active))

    def cancel(self, request_id: str) -> bool:
        with self._active_lock:
            process = self._active.get(request_id)
            if process is None:
                return False
            self._cancelled.add(request_id)
        self._terminate_process_tree(process)
        return True

    def prepare(self, request: CommandRequest) -> PreparedCommand:
        """Build the exact permission request persisted before process launch."""

        operation = self._operation(request)
        normalized_cwd, cwd = self.workspace.resolve(
            request.cwd, allow_root=True, must_exist=True
        )
        if not cwd.is_dir():
            raise CommandPreparationError(
                ErrorCode.WORKING_DIRECTORY_INVALID,
                "working directory is not a directory",
                resource=normalized_cwd or ".",
                risk_level=RiskLevel.HIGH,
            )

        assessment = self.command_policy.assess(request)
        if not assessment.allowed or assessment.executable is None:
            raise CommandPreparationError(
                assessment.error_code or ErrorCode.COMMAND_NOT_ALLOWED,
                assessment.reason,
                resource=request.executable,
                risk_level=assessment.risk_level,
            )

        permission_request = PermissionRequest.create(
            project_id=self.workspace.project_id,
            actor=request.actor,
            capability=request.capability,
            resource=str(assessment.executable),
            operation=operation,
            risk_level=assessment.risk_level,
            metadata={
                "executable": str(assessment.executable),
                "argument_count": len(request.args),
                "args_digest": _mapping_digest(list(request.args)),
                "cwd": normalized_cwd or ".",
                "environment_keys": sorted(request.environment),
                "environment_digest": _mapping_digest(dict(request.environment)),
                "network_policy": request.network_policy.value,
                "timeout_seconds": request.limits.timeout_seconds,
                "output_limit_bytes_per_stream": request.limits.max_output_bytes_per_stream,
                "shell": False,
            },
            request_id=request.request_id,
            task_id=request.task_id,
        )
        return PreparedCommand(
            operation=operation,
            normalized_cwd=normalized_cwd,
            cwd=cwd,
            assessment=assessment,
            permission_request=permission_request,
        )

    def run(
        self,
        request: CommandRequest,
        *,
        approval: ApprovalGrant | None = None,
        network_approval: ApprovalGrant | None = None,
    ) -> ToolResult[dict[str, Any]]:
        operation = self._operation(request)
        try:
            prepared = self.prepare(request)
        except WorkspaceViolation as exc:
            return self._reject(
                request, operation, exc.code, str(exc), resource=request.cwd or ".", risk=RiskLevel.HIGH
            )
        except CommandPreparationError as exc:
            return self._reject(
                request,
                operation,
                exc.code,
                str(exc),
                resource=exc.resource,
                risk=exc.risk_level,
            )
        normalized_cwd = prepared.normalized_cwd
        cwd = prepared.cwd
        assessment = prepared.assessment

        self.authorization.audit_log.redactor.add_secret_values(
            self.environment_sanitizer.secret_values(request.environment)
        )

        authorization = self.authorization.authorize_request(
            prepared.permission_request,
            approval=approval,
        )
        denied = self._authorization_failure(authorization)
        if denied:
            return denied

        if request.network_policy != NetworkPolicy.DENIED:
            network_id = f"{request.request_id}:network"
            network = self.authorization.authorize(
                actor=request.actor,
                capability=Capability.NETWORK_ACCESS,
                resource=str(assessment.executable),
                operation="execution.network",
                risk_level=RiskLevel.HIGH,
                metadata={
                    "parent_request_id": request.request_id,
                    "parent_request_digest": authorization.request.digest(),
                },
                request_id=network_id,
                task_id=request.task_id,
                approval=network_approval,
            )
            network_denied = self._authorization_failure(network)
            if network_denied:
                return network_denied

        if not self._project_lock.acquire(blocking=False):
            return self._authorized_failure(
                authorization, ErrorCode.PROJECT_BUSY, "another execution is active for this project"
            )

        try:
            started_at = perf_counter()
            started = self.authorization.record_started(
                authorization,
                metadata={
                    "executable": str(assessment.executable),
                    "argument_count": len(request.args),
                    "args_digest": _mapping_digest(list(request.args)),
                    "cwd": normalized_cwd or ".",
                    "shell": False,
                },
            )
            try:
                environment, output_redactor = self.environment_sanitizer.build(
                    self.workspace, assessment.executable, request.environment
                )
            except ValueError as exc:
                return self._finished_failure(
                    authorization, started, started_at, ErrorCode.PERMISSION_DENIED, str(exc)
                )

            stdout = _BoundedBuffer(request.limits.max_output_bytes_per_stream)
            stderr = _BoundedBuffer(request.limits.max_output_bytes_per_stream)
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            try:
                process = subprocess.Popen(
                    [str(assessment.executable), *request.args],
                    cwd=cwd,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=False,
                    creationflags=creationflags,
                    start_new_session=os.name != "nt",
                )
            except OSError as exc:
                return self._finished_failure(
                    authorization, started, started_at, ErrorCode.PROCESS_START_FAILED, str(exc)
                )

            with self._active_lock:
                self._active[request.request_id] = process
            stdout_thread = threading.Thread(target=stdout.consume, args=(process.stdout,), daemon=True)
            stderr_thread = threading.Thread(target=stderr.consume, args=(process.stderr,), daemon=True)
            stdout_thread.start()
            stderr_thread.start()

            timed_out = False
            try:
                exit_code = process.wait(timeout=request.limits.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._terminate_process_tree(process)
                try:
                    exit_code = process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    exit_code = process.wait(timeout=5)
            finally:
                stdout_thread.join(timeout=10)
                stderr_thread.join(timeout=10)
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()
                with self._active_lock:
                    cancelled = request.request_id in self._cancelled
                    self._cancelled.discard(request.request_id)
                    self._active.pop(request.request_id, None)

            stdout_text = output_redactor.redact_text(stdout.text())
            stderr_text = output_redactor.redact_text(stderr.text())
            result_data = {
                "executable": str(assessment.executable),
                "args": list(request.args),
                "cwd": normalized_cwd or ".",
                "exit_code": exit_code,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "duration_seconds": round(perf_counter() - started_at, 6),
                "stdout_truncated": stdout.truncated,
                "stderr_truncated": stderr.truncated,
                "shell": False,
                "network_policy": request.network_policy.value,
                "network_enforcement": "DECLARATIVE_ONLY",
                "memory_limit_enforced": False,
                "process_count_limit_enforced": False,
            }

            if cancelled:
                return self._finished_failure(
                    authorization, started, started_at, ErrorCode.EXECUTION_CANCELLED,
                    "execution was cancelled", status=AuditStatus.CANCELLED, exit_code=exit_code,
                    result=result_data,
                )
            if timed_out:
                return self._finished_failure(
                    authorization, started, started_at, ErrorCode.EXECUTION_TIMEOUT,
                    f"execution timed out after {request.limits.timeout_seconds}s",
                    exit_code=exit_code, result=result_data,
                )
            if exit_code != 0:
                return self._finished_failure(
                    authorization, started, started_at, ErrorCode.PROCESS_EXIT_NONZERO,
                    f"process exited with code {exit_code}", exit_code=exit_code, result=result_data,
                )

            finished = self.authorization.record_finished(
                authorization, started, status=AuditStatus.COMPLETED,
                duration_seconds=perf_counter() - started_at, exit_code=exit_code,
                metadata={
                    "stdout_bytes_returned": len(stdout_text.encode("utf-8")),
                    "stderr_bytes_returned": len(stderr_text.encode("utf-8")),
                    "stdout_truncated": stdout.truncated,
                    "stderr_truncated": stderr.truncated,
                },
            )
            return ToolResult.ok(result_data, meta=self._meta(authorization, finished))
        finally:
            self._project_lock.release()

    @staticmethod
    def _operation(request: CommandRequest) -> str:
        return "test.run" if request.capability == Capability.TEST_EXECUTE else "execution.run"

    def _terminate_process_tree(self, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                shell=False,
            )
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if process.poll() is None:
            process.kill()

    def _reject(
        self, request: CommandRequest, operation: str, code: ErrorCode,
        message: str, *, resource: str, risk: RiskLevel,
    ) -> ToolResult[Any]:
        record = self.authorization.reject_before_authorization(
            actor=request.actor, capability=request.capability, resource=resource,
            operation=operation, risk_level=risk, error_code=code, reason=message,
            request_id=request.request_id, task_id=request.task_id,
        )
        return ToolResult.fail(
            code, message, details={"resource": resource}, meta=self._meta(record, record.decision_event)
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

    def _authorized_failure(
        self, record: AuthorizationRecord, code: ErrorCode, message: str
    ) -> ToolResult[Any]:
        started_at = perf_counter()
        started = self.authorization.record_started(record)
        return self._finished_failure(record, started, started_at, code, message)

    def _finished_failure(
        self, record: AuthorizationRecord, started: AuditEvent, started_at: float,
        code: ErrorCode, message: str, *, status: AuditStatus = AuditStatus.FAILED,
        exit_code: int | None = None, result: dict[str, Any] | None = None,
    ) -> ToolResult[Any]:
        finished = self.authorization.record_finished(
            record, started, status=status, duration_seconds=perf_counter() - started_at,
            exit_code=exit_code, error_code=code,
            metadata={
                "message": message,
                "stdout_bytes_returned": len(str((result or {}).get("stdout", "")).encode("utf-8")),
                "stderr_bytes_returned": len(str((result or {}).get("stderr", "")).encode("utf-8")),
                "stdout_truncated": bool((result or {}).get("stdout_truncated", False)),
                "stderr_truncated": bool((result or {}).get("stderr_truncated", False)),
            },
        )
        details = dict(result or {})
        return ToolResult.fail(code, message, details=details, meta=self._meta(record, finished))

    def _meta(self, record: AuthorizationRecord, event: AuditEvent) -> dict[str, str]:
        return {
            **self.workspace.result_meta(),
            "request_id": record.request.request_id,
            "permission_request_digest": record.request.digest(),
            "audit_event_id": event.event_id,
        }


def _mapping_digest(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
