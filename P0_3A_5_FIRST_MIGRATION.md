# P0-3A.5 First Production Control-Plane Migration

**Baseline:** `dd55f31e1acfd1101adb732cacd3290ad021b273`

**Review branch:** `review/p0-3a5-first-migration`

**Migration target:** `apos validate <taskspec>`

## Purpose

This change migrates one existing production CLI command from a direct file read to the modern APOS control plane. It is a deliberately small vertical slice, not a general CLI rewrite or a P0-3B implementation.

`apos validate` was selected because its existing behavior is read-only and requires no process, Git, network, Ollama, or project-file mutation capability.

## Current Call Graph

Before this migration, the production path was:

```text
apos validate <taskspec>
    -> apos.cli.cmd_validate
    -> TaskSpec.load(Path)
    -> pathlib.Path.read_text
    -> json.loads
    -> TaskSpec.from_mapping / validate
    -> console output
```

The command did not call `Kernel`, the legacy executor, `GitClient`, Ollama, or the network. Its project file read nevertheless bypassed `ProjectWorkspace`, `AuthorizationService`, `FileSystemService`, and `AuditLog`.

## Target Call Graph

```text
apos validate <project-relative-taskspec>
    -> thin CLI adapter
    -> ProjectRuntime
    -> TaskSpecValidationService
    -> FileSystemService.read_file
    -> ProjectWorkspace.resolve / SecretPolicy
    -> AuthorizationService
    -> PermissionEngine
    -> AuditLog
    -> TaskSpec.from_mapping / validate
    -> ToolResult
    -> existing console output
```

## Implemented Call Graph

The implemented path matches the target graph:

1. `cmd_validate` requests the centralized `ProjectRuntime.create_read_only` production profile rooted at the current working directory.
2. The reviewed runtime profile allows only `PROJECT_READ`; every unspecified capability fails closed. The CLI adapter does not define its own authority policy.
3. The local CLI request is attributed to `USER:local-cli`. This is actor attribution, not authenticated human identity proof.
4. `TaskSpecValidationService` asks `FileSystemService` to read the project-relative TaskSpec.
5. `FileSystemService` applies workspace and secret-path checks, requests authorization, records the audit lifecycle, and returns a `ToolResult`.
6. The validation service parses and validates the authorized content and returns a structured success or error result.
7. The CLI preserves the existing Korean success message and converts a structured failure into the existing `SpecError` command-error path.

## Task And Approval Semantics

This read-only operation is not persisted as a P0-3A task. Creating a durable task and human approval solely to read a user-selected TaskSpec would impose the write/execution lifecycle on a low-risk inspection command without adding a meaningful security guarantee.

The runtime still composes `TaskService` as its official persistent task control plane, but this slice relies on the explicit `PROJECT_READ=ALLOW` policy and request ID correlation. It does not fabricate a persistent task ID or an approval grant.

Authenticated human identity remains unimplemented. The `USER:local-cli` actor means only that the local CLI adapter originated the request.

## Runtime And Recovery Lifecycle

Generic `ProjectRuntime` and `TaskService` construction initializes and wires the persistent repository but does not recover or otherwise transition existing tasks. This makes runtime composition safe for an unrelated read-only command.

Crash recovery remains available through the explicit `ProjectRuntime.recover_interrupted_tasks` authority boundary. A dedicated task execution owner calls this operation during its controlled startup, after it has determined that recorded `RUNNING` tasks belong to an interrupted prior execution lifecycle. The operation transitions those tasks to `RECOVERY_REQUIRED` and flushes their persistent outbox events to the audit log.

P0-3A does not yet provide authenticated owner identity or an OS-enforced single-owner lease. The explicit method separates recovery authority from generic construction without claiming those later guarantees.

## Project Boundary

The migrated command deliberately accepts only a project-relative TaskSpec path rooted at the current working directory. Absolute paths, traversal paths, links that resolve outside the project, `.apos`, `.git`, and secret-classified paths fail closed through `ProjectWorkspace` and `SecretPolicy`.

This narrows the former unrestricted `Path.read_text` behavior. A TaskSpec outside the current project is no longer readable through `apos validate`.

The command does not mutate the TaskSpec, project source, or Git state. `ProjectRuntime` may create APOS-internal state under `.apos/state`, and the required audit lifecycle is appended under `.apos/audit`.

## Structured Result Contract

On success, `TaskSpecValidationService.validate` returns a `ToolResult` containing:

- normalized project-relative path;
- TaskSpec ID;
- display title;
- validated TaskSpec mapping;
- project, request, authorization digest, and audit correlation metadata inherited from the filesystem result.

Project-boundary, authorization, filesystem, decoding, JSON, and TaskSpec schema failures remain structured `ToolResult` failures. The CLI exposes them through its established one-line `APOS 오류` behavior rather than a raw traceback.

Expected runtime initialization failures represented by `TaskError` or `WorkspaceViolation` also terminate through a stable error code and one-line CLI message. Programmer errors are not caught by this domain-error boundary.

The audit lifecycle in this slice records the privileged filesystem operation. A JSON or TaskSpec schema rejection occurs after an authorized read and is returned as a structured validation error, but no separate non-privileged validation outcome event is added. Command-level outcome correlation is deferred because adding a second audit operation is not required to secure the project read and would expand this review fix beyond its purpose.

## Security And Architecture Verification

The regression tests verify behavior rather than relying only on source-string inspection:

- a real `main(["validate", ...])` call creates APOS internal state and records `REQUESTED`, `AUTHORIZED`, `STARTED`, and `COMPLETED` audit events for `filesystem.read`;
- every event carries one request ID, `PROJECT_READ`, the normalized resource, and the local CLI actor;
- the TaskSpec bytes remain unchanged;
- a path outside the project returns `PATH_OUTSIDE_PROJECT` and records `REQUESTED`, `DENIED`;
- invalid JSON returns a structured `INVALID_ARGUMENT` result after an authorized and audited read;
- an unrelated persistent task remains `RUNNING` when the real production validate command creates its read-only runtime;
- `RUNNING` tasks transition to `RECOVERY_REQUIRED` only when the explicit runtime recovery authority is invoked;
- a corrupted task store returns a stable non-zero CLI error without traceback or absolute project-path disclosure;
- the centralized read-only profile allows project reads while denying project writes;
- spies fail the test if the adapter calls `TaskSpec.load`, `Kernel`, `GitClient`, or direct `subprocess.run`.

## Explicit Non-Scope

This migration does not add or change:

- P0-3B `ExecutionBroker` or mandatory repository-wide dispatcher;
- process execution, cancellation, Job Objects, restricted tokens, or OS sandboxing;
- Git operations or rollback;
- network, Ollama, MCP, Discord, API, GUI, or remote control;
- persistent task lifecycle or human authentication;
- any other production command.

Legacy modules remain present for commands that have not yet migrated. This slice proves one production adapter path only; it does not claim that the repository-wide mandatory control-plane invariant is already complete.
