# P0-4A Application Service

P0-4A adds the official Python application boundary for APOS development task execution.
The service lets callers use the production-safe task workflow without importing CLI
handlers, spawning `apos` as a subprocess, or manually reconstructing Kernel factories.

## Boundary

The intended dependency direction is:

```text
External caller or CLI
-> APOSApplicationService
-> production-safe runtime/factories
-> Kernel
-> Controlled Execution / Git / Tests
```

The future MCP server should call this boundary directly. This phase does not implement
MCP, MCP transport, background jobs, external approval UX, or authenticated users.

## Runtime Construction

`APOSApplicationService.run_task(...)` constructs `Kernel` with the same controlled
dependencies used by the production CLI path:

- `ProjectRuntime.create_local_test_execution(...)` for TaskSpec test commands.
- `ProjectRuntime.create_local_git_phase_a(...)` for controlled Git reads, branch
  preparation, patch apply/rollback, index staging, and commit.
- `ControlledGitClient` as the Kernel Git adapter.
- A local `USER:local-cli` actor by default, or a caller-provided actor.

This prevents programmatic callers from accidentally using bare `Kernel(root)`, whose
default factories remain the legacy compatibility path.

## Public Methods

`APOSApplicationService` exposes:

- `validate_task(path)` for structured TaskSpec validation through the read-only runtime.
- `run_task(spec, options)` for direct `TaskSpec` execution through production-safe Kernel wiring.
- `run_task_file(path, options)` for file-based validation followed by execution.
- `get_status()` for structured APOS status data.
- `list_runs(limit)` for structured run-log summaries.
- `get_run(run_path)` for structured persisted run details.

The service returns existing APOS domain objects where possible, including `RunSummary`
and `RunLogEntry`. `APOSStatus` is a small structured status object because the CLI
status command was previously human-text oriented.

## CLI Relationship

The CLI remains responsible for argument parsing and human-readable formatting. The
`run`, `validate`, `status`, `runs list`, and `runs show` handlers delegate to
`APOSApplicationService` so the CLI and future MCP integration share the same application
boundary.

## Known Limitations

- Execution is synchronous.
- Run status is inspected from persisted run logs; no background task system is added.
- External approval UX is not exposed.
- File replacement still uses the existing Kernel file write path.
- Benchmark and evolution flows are unchanged.
