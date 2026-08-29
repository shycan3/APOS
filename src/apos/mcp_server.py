from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from .application import APOSApplicationService
from .core.result import ErrorCode, ToolResult
from .kernel import KernelError, RunOptions
from .models import RunSummary, SpecError, TaskSpec


DEFAULT_RUN_LIMIT = 20
MAX_RUN_LIMIT = 100


class APOSMCPTools:
    """Thin MCP tool adapter over the production-safe APOS application service."""

    def __init__(self, service: APOSApplicationService) -> None:
        self.service = service

    def apos_status(self) -> dict[str, Any]:
        return _ok(status=self.service.get_status().to_dict())

    def apos_validate_task(
        self,
        task_path: str | None = None,
        task: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        source_error = _validate_task_source(task_path, task)
        if source_error is not None:
            return source_error

        if task_path is not None:
            path_error = _validate_non_empty_string("task_path", task_path)
            if path_error is not None:
                return path_error
            return _tool_result_to_response(self.service.validate_task(task_path), data_key="validation")

        try:
            spec = _task_spec_from_inline(task)
        except SpecError as exc:
            return _error("INVALID_TASK", str(exc))
        return _ok(validation={"taskspec": spec.to_dict(), "source": "inline"})

    def apos_run_task(
        self,
        task_path: str | None = None,
        task: dict[str, Any] | None = None,
        no_commit: bool = False,
        command_timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        source_error = _validate_task_source(task_path, task)
        if source_error is not None:
            return source_error

        options_or_error = _run_options(no_commit, command_timeout_seconds)
        if isinstance(options_or_error, dict):
            return options_or_error
        options = options_or_error

        try:
            if task_path is not None:
                path_error = _validate_non_empty_string("task_path", task_path)
                if path_error is not None:
                    return path_error
                summary = self.service.run_task_file(task_path, options)
            else:
                summary = self.service.run_task(_task_spec_from_inline(task), options)
        except SpecError as exc:
            return _error("INVALID_TASK", str(exc))
        except KernelError as exc:
            return _error("EXECUTION_ERROR", str(exc))
        except FileNotFoundError as exc:
            return _error("TASK_NOT_FOUND", str(exc))
        except Exception as exc:
            return _error("INTERNAL_ERROR", "APOS run task failed unexpectedly", {"type": type(exc).__name__})

        return _ok(summary=_summary_to_dict(summary))

    def apos_get_run(self, run_path: str) -> dict[str, Any]:
        path_error = _validate_non_empty_string("run_path", run_path)
        if path_error is not None:
            return path_error
        try:
            return _ok(run=self.service.get_run(run_path))
        except FileNotFoundError as exc:
            return _error("RUN_NOT_FOUND", str(exc))
        except Exception as exc:
            return _error("INTERNAL_ERROR", "APOS get run failed unexpectedly", {"type": type(exc).__name__})

    def apos_list_runs(self, limit: int = DEFAULT_RUN_LIMIT) -> dict[str, Any]:
        limit_or_error = _run_limit(limit)
        if isinstance(limit_or_error, dict):
            return limit_or_error
        try:
            return _ok(runs=[entry.to_dict() for entry in self.service.list_runs(limit=limit_or_error)])
        except Exception as exc:
            return _error("INTERNAL_ERROR", "APOS list runs failed unexpectedly", {"type": type(exc).__name__})


def build_server(root: str | Path | None = None, service: APOSApplicationService | None = None) -> MCPServer:
    fixed_root = Path.cwd().resolve() if root is None else Path(root).resolve()
    tools = APOSMCPTools(service or APOSApplicationService(fixed_root))
    server = MCPServer(
        "apos",
        title="APOS",
        description="APOS local orchestration tools over the production-safe application service.",
    )
    server.tool()(tools.apos_status)
    server.tool()(tools.apos_validate_task)
    server.tool()(tools.apos_run_task)
    server.tool()(tools.apos_get_run)
    server.tool()(tools.apos_list_runs)
    return server


def main() -> None:
    build_server().run(transport="stdio")


def _validate_task_source(task_path: str | None, task: dict[str, Any] | None) -> dict[str, Any] | None:
    if (task_path is None) == (task is None):
        return _error(
            "INVALID_TASK_SOURCE",
            "Provide exactly one of task_path or task.",
            {"fields": ["task_path", "task"]},
        )
    return None


def _validate_non_empty_string(field: str, value: str) -> dict[str, Any] | None:
    if not isinstance(value, str) or not value.strip():
        return _error("INVALID_ARGUMENT", f"{field} must be a non-empty string.", {"field": field})
    return None


def _task_spec_from_inline(task: dict[str, Any] | None) -> TaskSpec:
    if not isinstance(task, dict):
        raise SpecError("task must be a JSON object")
    return TaskSpec.from_mapping(task)


def _run_options(no_commit: bool, command_timeout_seconds: int | None) -> RunOptions | dict[str, Any]:
    if not isinstance(no_commit, bool):
        return _error("INVALID_ARGUMENT", "no_commit must be a boolean.", {"field": "no_commit"})
    if command_timeout_seconds is not None:
        if isinstance(command_timeout_seconds, bool) or not isinstance(command_timeout_seconds, int):
            return _error(
                "INVALID_ARGUMENT",
                "command_timeout_seconds must be a positive integer.",
                {"field": "command_timeout_seconds"},
            )
        if command_timeout_seconds < 1:
            return _error(
                "INVALID_ARGUMENT",
                "command_timeout_seconds must be a positive integer.",
                {"field": "command_timeout_seconds"},
            )
    return RunOptions(no_commit=no_commit, command_timeout_seconds=command_timeout_seconds)


def _run_limit(limit: int) -> int | dict[str, Any]:
    if isinstance(limit, bool) or not isinstance(limit, int):
        return _error("INVALID_ARGUMENT", "limit must be an integer.", {"field": "limit"})
    if limit < 1 or limit > MAX_RUN_LIMIT:
        return _error(
            "INVALID_ARGUMENT",
            f"limit must be between 1 and {MAX_RUN_LIMIT}.",
            {"field": "limit", "min": 1, "max": MAX_RUN_LIMIT},
        )
    return limit


def _tool_result_to_response(result: ToolResult[dict[str, Any]], *, data_key: str) -> dict[str, Any]:
    if result.success:
        return _ok(**{data_key: result.data})
    assert result.error is not None
    return {"success": False, "error": result.error.to_dict()}


def _summary_to_dict(summary: RunSummary) -> dict[str, Any]:
    return summary.to_dict()


def _ok(**payload: Any) -> dict[str, Any]:
    return {"success": True, **payload}


def _error(code: str | ErrorCode, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "success": False,
        "error": {
            "code": code.value if isinstance(code, ErrorCode) else code,
            "message": message,
            "details": dict(details or {}),
        },
    }


if __name__ == "__main__":
    main()
