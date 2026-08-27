from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar


class ErrorCode(str, Enum):
    """Stable machine-readable errors returned by APOS tools."""

    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    PATH_OUTSIDE_PROJECT = "PATH_OUTSIDE_PROJECT"
    PATH_NOT_FOUND = "PATH_NOT_FOUND"
    PATH_NOT_FILE = "PATH_NOT_FILE"
    PATH_NOT_DIRECTORY = "PATH_NOT_DIRECTORY"
    SECRET_PATH_DENIED = "SECRET_PATH_DENIED"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    DECODE_FAILED = "DECODE_FAILED"
    IO_ERROR = "IO_ERROR"
    PERMISSION_REQUIRED = "PERMISSION_REQUIRED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    POLICY_EVALUATION_FAILED = "POLICY_EVALUATION_FAILED"
    COMMAND_NOT_ALLOWED = "COMMAND_NOT_ALLOWED"
    WORKING_DIRECTORY_INVALID = "WORKING_DIRECTORY_INVALID"
    NETWORK_ACCESS_DENIED = "NETWORK_ACCESS_DENIED"
    EXECUTION_TIMEOUT = "EXECUTION_TIMEOUT"
    EXECUTION_CANCELLED = "EXECUTION_CANCELLED"
    PROCESS_START_FAILED = "PROCESS_START_FAILED"
    PROCESS_EXIT_NONZERO = "PROCESS_EXIT_NONZERO"
    OUTPUT_LIMIT_EXCEEDED = "OUTPUT_LIMIT_EXCEEDED"
    PROJECT_BUSY = "PROJECT_BUSY"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True)
class ToolError:
    code: ErrorCode
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "details": dict(self.details),
        }


T = TypeVar("T")


@dataclass(frozen=True)
class ToolResult(Generic[T]):
    """Common result envelope shared by every transport adapter."""

    success: bool
    data: T | None = None
    error: ToolError | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.success == (self.error is not None):
            raise ValueError("successful results cannot contain an error and failed results must contain one")

    @classmethod
    def ok(cls, data: T, *, meta: dict[str, Any] | None = None) -> "ToolResult[T]":
        return cls(success=True, data=data, meta=dict(meta or {}))

    @classmethod
    def fail(
        cls,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> "ToolResult[T]":
        return cls(
            success=False,
            error=ToolError(code=code, message=message, details=dict(details or {})),
            meta=dict(meta or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error.to_dict() if self.error else None,
            "meta": dict(self.meta),
        }
