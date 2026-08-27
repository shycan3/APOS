from __future__ import annotations

import json
from typing import Any

from ..models import SpecError, TaskSpec
from .filesystem import FileSystemService
from .permissions import Actor
from .result import ErrorCode, ToolResult


class TaskSpecValidationService:
    """Read and validate a project-scoped TaskSpec through the modern filesystem boundary."""

    def __init__(self, filesystem: FileSystemService) -> None:
        self.filesystem = filesystem

    def validate(
        self,
        path: str,
        *,
        actor: Actor,
        request_id: str | None = None,
        task_id: str | None = None,
    ) -> ToolResult[dict[str, Any]]:
        read_result = self.filesystem.read_file(
            path,
            actor=actor,
            request_id=request_id,
            task_id=task_id,
        )
        if not read_result.success:
            return ToolResult(
                success=False,
                error=read_result.error,
                meta=dict(read_result.meta),
            )

        assert read_result.data is not None
        try:
            payload = json.loads(read_result.data["content"])
        except json.JSONDecodeError as exc:
            return ToolResult.fail(
                ErrorCode.INVALID_ARGUMENT,
                f"invalid JSON in {read_result.data['path']}: {exc}",
                details={"path": read_result.data["path"]},
                meta=read_result.meta,
            )
        if not isinstance(payload, dict):
            return ToolResult.fail(
                ErrorCode.INVALID_ARGUMENT,
                "TaskSpec must be a JSON object",
                details={"path": read_result.data["path"]},
                meta=read_result.meta,
            )

        try:
            spec = TaskSpec.from_mapping(payload)
        except SpecError as exc:
            return ToolResult.fail(
                ErrorCode.INVALID_ARGUMENT,
                str(exc),
                details={"path": read_result.data["path"]},
                meta=read_result.meta,
            )

        return ToolResult.ok(
            {
                "path": read_result.data["path"],
                "task_id": spec.task_id,
                "title": spec.display_title(),
                "taskspec": spec.to_dict(),
            },
            meta=read_result.meta,
        )
