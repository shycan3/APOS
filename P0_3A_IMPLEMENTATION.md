# APOS P0-3A Implementation Report

## Scope

P0-3A adds persistent task and approval lifecycle management to the P0-2 core. It does not add an OS sandbox, network sandbox, Git checkpoint/rollback, MCP, Discord, GUI, remote server, cloud backend, or autonomous self-modification.

The implementation is based on the P0-2 `master` state and preserves all previously documented P0-2 execution limitations.

## Architecture Changes

`ProjectRuntime` remains the composition root and now assembles:

```text
ProjectRuntime
|-- ProjectWorkspace
|-- AuditLog
|-- TaskService (official task control plane)
|   |-- _repository: SQLiteTaskRepository (infrastructure detail)
|   `-- _execution_service: ControlledExecutionService
|-- PermissionEngine
|   `-- persistent approval consumer: TaskService
|-- AuthorizationService
|-- FileSystemService
|-- ControlledExecutionService
`-- ToolRegistry
```

`ProjectRuntime` does not expose `task_repository`. Its official task interaction path is `ProjectRuntime.tasks`, and the service keeps its repository under the private-by-convention `_repository` attribute. Python does not provide a security-grade private boundary, so this is an API and architecture boundary rather than protection against malicious in-process code.

`TaskService.run_command_task()` uses the `ControlledExecutionService` bound by the runtime composition root. Adapters do not supply an executor. The process launch still passes through `AuthorizationService` and `PermissionEngine`.

The domain layer depends on the `TaskRepository` protocol. SQLite is one implementation and can be replaced without changing task state or approval semantics. Repository types remain importable from `apos.core.tasks` for infrastructure construction and persistence tests, but are not exported from the top-level `apos.core` API.

## Task State Machine

States:

- `CREATED`
- `QUEUED`
- `WAITING_APPROVAL`
- `APPROVED`
- `RUNNING`
- `SUCCEEDED`
- `FAILED`
- `CANCELLED`
- `EXPIRED`
- `RECOVERY_REQUIRED`

Allowed transitions:

| Current | Allowed targets |
|---|---|
| `CREATED` | `QUEUED`, `CANCELLED`, `EXPIRED` |
| `QUEUED` | `WAITING_APPROVAL`, `CANCELLED`, `EXPIRED` |
| `WAITING_APPROVAL` | `APPROVED`, `CANCELLED`, `EXPIRED` |
| `APPROVED` | `RUNNING`, `CANCELLED`, `EXPIRED` |
| `RUNNING` | `SUCCEEDED`, `FAILED`, `CANCELLED`, `RECOVERY_REQUIRED` |
| `RECOVERY_REQUIRED` | `FAILED`, `CANCELLED` |
| Terminal states | No transitions |

`APPROVED` can be reached only through persistent approval issuance. `RUNNING` can be reached only through the atomic persistent approval-consumption transaction. Generic transition calls cannot skip from `WAITING_APPROVAL` to `RUNNING`.

A `RUNNING` task cannot be marked `CANCELLED` unless the caller confirms that its existing execution has stopped. P0-3A does not implement an execution supervisor.

## Persistent Task Model

Each task stores:

- task and project IDs;
- creation/update timestamps and per-state timestamps;
- subject actor;
- redacted description and metadata;
- requested capability;
- current state and optimistic version;
- permission-request ID and optional approval ID;
- retry count;
- optional failure and cancellation information.

Task IDs and permission-request IDs are unique. A persistent task requires `PermissionRequest.task_id`; because task ID is part of the canonical request digest, changing only the task ID invalidates the approval.

## Persistence Model

Backend: SQLite using Python's standard `sqlite3` module.

Default location:

```text
.apos/state/tasks.sqlite3
```

The path must resolve below the project root. `.apos/state/` is ignored by Git.

SQLite was selected instead of JSON file dumps because it provides:

- atomic multi-row transactions;
- uniqueness and foreign-key constraints;
- `BEGIN IMMEDIATE` serialization for competing writers;
- WAL journaling and `synchronous=FULL` durability;
- schema versioning through `PRAGMA user_version`;
- `quick_check` corruption detection;
- optimistic task versions; and
- one transaction for approval consumption plus task execution claim.

Schema version 1 contains:

- `permission_requests`: canonical non-secret request data and SHA-256 digest;
- `tasks`: state, ownership, timestamps, failure/cancellation data, and version;
- `approvals`: exact request/task/subject binding, source, expiry, and consumption state;
- `task_events`: transactional audit outbox with stable event IDs.

Permission requests containing values that the shared redactor classifies as secret are rejected. Built-in operations must persist digests and non-secret metadata rather than raw content, command arguments, credentials, or environment values.

## Approval Model

Persistent approval binds all of the following:

- approval ID;
- task ID;
- permission-request ID;
- project ID;
- canonical permission-request digest;
- task subject actor;
- approver actor;
- approval source;
- issuance and optional expiry timestamps.

When `PermissionEngine` evaluates a persistent task request, it delegates approval consumption to `TaskService`. SQLite atomically verifies the stored request/grant/task, marks the grant consumed, and changes the task from `APPROVED` to `RUNNING`.

The transaction rejects:

- a changed resource, operation, capability, actor, metadata, or task ID;
- a different or missing approval ID;
- subject mismatch;
- an expired grant;
- a consumed grant;
- a task not in `APPROVED`; and
- a concurrent second claim.

If a capability policy would ordinarily return `ALLOW`, a request associated with an existing persistent task still requires and consumes that task's persistent approval.

## Human Approval Boundary

Approval sources are explicit:

- `UNAUTHENTICATED_USER_REQUEST`
- `AUTHENTICATED_HUMAN`

`ApprovalGrant` represents human-originated approval intent only. It cannot be issued by `SYSTEM`, `EXTERNAL_AI`, or `LOCAL_LLM`. A deterministic policy result is a `PermissionDecision` and is system authorization, not human approval. A policy `ALLOW` therefore does not create, imitate, or consume an `ApprovalGrant`, except that an existing persistent task still independently requires its one-time task approval.

P0-3A supplies `LocalUnauthenticatedHumanApprovalBoundary`. It permits only an explicit local `USER` action marked `UNAUTHENTICATED_USER_REQUEST`. This records a local user request but does not prove who the human is.

It rejects:

- `EXTERNAL_AI`, `LOCAL_LLM`, and `SYSTEM` actors attempting to issue human approval; and
- `AUTHENTICATED_HUMAN` or `authenticated=True`, including direct `ApprovalGrant` construction, because identity proof is not implemented.

This local boundary is not password authentication, OS identity proof, MFA, a signature, or non-repudiation. It must not be exposed as a remote approval API.

## Crash Recovery

### WAITING_APPROVAL

The state remains `WAITING_APPROVAL` after restart.

### Approval issued

The task, request, and approval remain `APPROVED` and unconsumed after restart.

### Approval consumed

Approval consumption and `APPROVED -> RUNNING` occur in one transaction. A committed consumption remains consumed across restart and cannot be replayed.

### RUNNING at startup

`TaskService` initialization atomically changes every persisted `RUNNING` task to `RECOVERY_REQUIRED` and records `automatic_execution_resumed = false`.

No process is assumed alive and no operation is automatically re-executed. Recovery can only be resolved explicitly to `FAILED` or `CANCELLED` in P0-3A.

### Audit publication interruption

Task events are first committed to the SQLite outbox. Startup and later task operations replay unpublished events to `AuditLog` using stable event IDs. This reduces state/audit gaps, but the JSONL audit file remains subject to the P0-2 integrity limitations documented in `SECURITY_THREAT_MODEL.md`.

SQLite transactions protect task/outbox state across local processes sharing the database. Audit publication does not have the same guarantee: event lookup scans the JSONL file, while deduplication and append locking are process-local. Two processes can race between lookup and append, duplicate a stable event ID, or produce ordering ambiguity. No OS file lock, global sequencer, external queue, or audit database is implemented. Current tests cover same-process replay and runtime restart behavior, not simultaneous cross-process JSONL writers.

## Concurrency Model

Every state-changing repository operation starts a SQLite `BEGIN IMMEDIATE` transaction. The task row also has an optimistic `version`.

Approval consumption uses conditional updates:

- approval row: only when `consumed_at IS NULL`;
- task row: only when version matches and state is `APPROVED`.

Two workers racing on one task are serialized by SQLite. Exactly one can consume the approval and claim `RUNNING`; the other observes a consumed approval or a non-approved task and cannot start execution.

This protects task and approval state across threads, runtime instances, and local processes sharing the SQLite file. It does not turn the existing P0-2 execution lock or JSONL audit lock into a global OS-level lock. Cross-process audit publication remains unresolved.

## Audit Integration

The following lifecycle event types are written through the SQLite outbox and mirrored to `AuditLog`:

- `TASK_CREATED`
- `TASK_QUEUED`
- `TASK_WAITING_APPROVAL`
- `APPROVAL_REQUESTED`
- `APPROVAL_GRANTED`
- `TASK_APPROVED`
- `APPROVAL_CONSUMED`
- `TASK_STARTED`
- `TASK_SUCCEEDED`
- `TASK_FAILED`
- `TASK_CANCELLED`
- `TASK_EXPIRED`
- `TASK_RECOVERY_REQUIRED`

Events carry stable task-event ID, task ID, request ID, optional approval ID, capability, actor, state metadata, and shared redaction. Raw credentials, content, command arguments, and environment values are not persisted as task-event data.

## Runtime and Execution Integration

`ProjectRuntime.create()` initializes the task store, performs crash recovery, publishes pending audit events, and supplies `TaskService` as the `PermissionEngine` persistent approval consumer.

The official adapter path is:

```text
External Adapter -> ProjectRuntime -> TaskService -> TaskRepository
                                      |
                                      -> AuthorizationService
                                         -> ControlledExecutionService
```

`ProjectRuntime.task_repository` and top-level repository exports are intentionally absent. Persistence tests may instantiate `SQLiteTaskRepository` directly from `apos.core.tasks`. This is not a claim that malicious Python code in the APOS process cannot inspect private attributes.

`TaskService.run_command_task()`:

1. verifies task actor, state, request ID, and approval reference;
2. calls the runtime-bound `ControlledExecutionService.run()` with the persisted grant;
3. relies on `AuthorizationService` and `PermissionEngine` for exact request authorization and atomic approval consumption;
4. maps the execution result to `SUCCEEDED`, `FAILED`, or confirmed `CANCELLED`.

If a crash occurs after approval consumption but before completion, startup changes `RUNNING` to `RECOVERY_REQUIRED` and never re-executes automatically.

## Security Invariants

| Invariant | P0-3A enforcement |
|---|---|
| No unapproved privileged task operation | Persistent task requests require an approval even when the capability policy is `ALLOW`; execution still uses `AuthorizationService`. |
| Exact request binding | Canonical `PermissionRequest.digest()` is stored and reconstructed; all request/task/grant fields are compared. |
| One-time approval | Conditional SQLite consumption is atomic. |
| Task ID alone is insufficient | Mutations require the subject actor; approval requires request ID, exact digest, subject, approver, source, and note. |
| AI/SYSTEM cannot create human approval | The P0-3A human boundary rejects these actors and sources. |
| Restart replay prevention | Consumption is durable in SQLite. |
| No RUNNING auto-resume | Startup changes it to `RECOVERY_REQUIRED`. |
| Grant is not execution | `APPROVED`, `APPROVAL_CONSUMED`, and `RUNNING` are distinct state/events. |
| Explicit transitions only | `TaskStateMachine` rejects all undefined transitions. |
| No new privileged bypass | Command task coordination calls only `ControlledExecutionService`. |
| Repository is not the runtime control plane | Runtime exposes `tasks`, not `task_repository`; generic repository transitions cannot enter `APPROVED` or `RUNNING`. |
| System authorization is not human approval | Policy `PermissionDecision` and human `ApprovalGrant` have separate meanings; AI/SYSTEM cannot construct human grants. |
| OS sandbox limits remain explicit | No sandbox claim is made. |
| Human authentication remains unimplemented | `AUTHENTICATED_HUMAN` is rejected by the default boundary. |

These invariants apply to the P0-3A persistent task path. Legacy APOS paths that do not use `ProjectRuntime` remain outside this guarantee and must not be exposed to untrusted callers.

## Tests Added

The original P0-3A task suite contains 21 tests. The review fix adds four security/architecture tests across task and permission suites, covering:

- creation, uniqueness, persistence, and legal/illegal transitions;
- WAITING and APPROVED restart recovery;
- exact request/task/actor binding;
- missing, expired, changed, consumed, and replayed approvals;
- unauthenticated and unavailable authenticated-human boundaries;
- AI/SYSTEM approval rejection;
- task-ID-only mutation rejection;
- policy-ALLOW persistent approval enforcement;
- concurrent two-worker approval consumption;
- audit event linkage and secret masking;
- corrupted SQLite detection;
- RUNNING recovery without automatic execution; and
- real `ProjectRuntime` to `ControlledExecutionService` integration.
- repository transition rejection for `APPROVED` and `RUNNING`;
- SYSTEM approval-grant rejection;
- authenticated-human fail-closed behavior for direct grant construction; and
- system authorization as a policy decision rather than a human grant.

## Verification Results

- Existing tests before P0-3A: 95
- New P0-3A tests before review fix: 21
- P0-3A review-fix tests: 4
- Review-fix new-test result: 4 passed, 2 subtests passed in 0.11 seconds
- Total: 120
- Final full-suite result: 120 passed, 5 subtests passed in 237.82 seconds
- Review-fix focused regression: 36 passed, 5 subtests passed
- Secret/redaction verification: 4 passed
- `compileall`: passed
- `git diff --check`: passed
- `shell=True` search: 0 matches in Python source and tests

## Files Added

- `src/apos/core/tasks.py`
- `tests/test_core_tasks.py`
- `P0_3A_IMPLEMENTATION.md`
- `SECURITY_THREAT_MODEL.md`

## Files Modified

- `.gitignore`
- `src/apos/core/__init__.py`
- `src/apos/core/audit.py`
- `src/apos/core/permissions.py`
- `src/apos/core/result.py`
- `src/apos/core/runtime.py`

## Files Deleted

None.

## Known Limitations

- Human authentication and identity proof are not implemented.
- The local unauthenticated approval boundary is not safe for remote exposure.
- OS-level filesystem, network, memory, and process-tree isolation are not implemented.
- Trusted interpreters can still escape the APOS logical project boundary after authorization.
- Legacy CLI/kernel/evolution/Git/Ollama paths are not migrated to the persistent runtime.
- SQLite protects state consistency but not a malicious process with the same OS user and direct database-file access.
- JSONL audit integrity remains non-cryptographic and lacks a cross-process writer lock.
- Stable audit event deduplication uses a JSONL scan and process-local locking; concurrent publishers can race or duplicate an event ID.
- Approval revocation, authenticated signatures, durable remote identity, and multi-host coordination are not implemented.
- P0-3A provides no automatic task retry. `retry_count` is persisted for future explicitly governed retry design.

## Future Human Authentication Boundary

A future implementation must replace the default boundary with a provider that verifies human identity outside the AI request channel and issues signed, expiring, audience-bound approval artifacts. Persistent source `AUTHENTICATED_HUMAN` must remain unavailable until that verification exists.

## Future MCP Integration Boundary

No MCP adapter exists in P0-3A. A future adapter must use one mandatory dispatcher, derive actor/project/capability server-side, prevent client-created approval authority, use `ProjectRuntime.tasks`, and prohibit access to legacy or direct privileged helpers.

## Explicitly Not Implemented

- P0-3B OS-level sandbox
- P0-3C Git checkpoint/rollback
- P0-3D MCP
- Discord or GUI integration
- Remote or cloud service
- Local LLM behavior changes
- Autonomous self-modification
- Commit or push
