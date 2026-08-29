# P0-4B MCP Server MVP

P0-4B adds a minimal Model Context Protocol server for APOS. The server is a transport adapter over the P0-4A `APOSApplicationService`; it does not call `Kernel` directly, does not invoke the legacy Git client, and does not shell out to `apos run`.

## Architecture

```text
MCP client
    -> stdio MCP transport
    -> apos.mcp_server.APOSMCPTools
    -> APOSApplicationService
    -> production-safe runtime factories
    -> Kernel
    -> controlled Git / controlled tests
```

The project root is fixed when the server starts. By default it is the process current working directory. Tool calls do not accept a project root and cannot select a different repository per request.

## Startup

Run the server from the repository root:

```bash
python -m apos.mcp_server
```

The MVP uses stdio only. HTTP, SSE, authentication, background jobs, streaming progress, cancellation, and remote execution are intentionally deferred.

## Tool Surface

### `apos_status`

Returns structured APOS status for the fixed root, including branch, dirty state, status porcelain, and whether a coder command is configured.

### `apos_validate_task`

Accepts exactly one of:

- `task_path`: a task file path resolved by `APOSApplicationService.validate_task`
- `task`: an inline task object parsed by `TaskSpec.from_mapping`

Inline tasks use the canonical APOS task model validation instead of duplicating validation rules in the MCP adapter.

### `apos_run_task`

Accepts exactly one of `task_path` or `task` using the same source rules as validation.

The only exposed run options are:

- `no_commit`
- `command_timeout_seconds`

Path-based runs delegate to `APOSApplicationService.run_task_file`. Inline runs construct a canonical `TaskSpec` with `TaskSpec.from_mapping` and delegate to `APOSApplicationService.run_task`.

### `apos_get_run`

Returns a persisted run log by path. Run-log path safety is delegated to the application service and run-log resolver.

### `apos_list_runs`

Returns recent persisted run summaries. The limit must be between 1 and 100.

## Result Contract

Transport or adapter failures return:

```json
{
  "success": false,
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable message",
    "details": {}
  }
}
```

APOS domain outcomes remain successful tool responses. For example, `FAILED`, `NEEDS_PERMISSION`, `PERMISSION_DENIED`, and `RECOVERY_REQUIRED` are returned as:

```json
{
  "success": true,
  "summary": {
    "status": "RECOVERY_REQUIRED"
  }
}
```

`RECOVERY_REQUIRED` is not flattened into an MCP transport failure because clients must be able to distinguish an APOS recovery state from a broken tool call.

## Deferred Work

The MVP intentionally does not implement HTTP or SSE transports, authentication, background execution, progress streaming, cancellation, benchmark migration, evolution migration, or additional Git operation migration.
