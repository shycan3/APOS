# P0-3A.6 TaskSpec Test Execution Migration

**Baseline:** `e9540429980080b475433952bc150a9af39346a2`

**Review branch:** `review/p0-3a6-test-execution-migration`

**Migration target:** TaskSpec test commands executed by production `apos run`

## Purpose

This checkpoint migrates one privileged production vertical slice into the modern APOS control plane. Every TaskSpec test command reached through `apos run` now has a persistent task, exact authorization request, one-time approval, controlled process launch, audit lifecycle, and structured result.

This is not a rewrite of `apos run`, a repository-wide mandatory dispatcher, or P0-3B sandboxing.

## Previous Production Call Graph

```text
apos run
    -> apos.cli.cmd_run
    -> Kernel.run_task
    -> executor.run_commands
    -> executor.run_command
    -> subprocess.run(shell=False)
    -> ExecutionResult
    -> existing Kernel retry / rollback / Git behavior
```

The legacy runner was called in three places: the preflight test, the post-file-replacement test, and the post-patch test. No persistent task, capability authorization, or modern execution audit guarded those process launches.

## New Production Call Graph

```text
apos run
    -> apos.cli.cmd_run
    -> Kernel(test_runner_factory=local modern adapter)
    -> TestExecutionSession.run_commands
    -> CommandRequest(TEST_EXECUTE, NETWORK_DENIED)
    -> TaskService.create_command_task
    -> ControlledExecutionService.prepare
    -> persistent PermissionRequest / task
    -> QUEUED -> WAITING_APPROVAL -> APPROVED
    -> TaskService.run_command_task
    -> AuthorizationService / PermissionEngine
    -> atomic approval consumption and APPROVED -> RUNNING
    -> ControlledExecutionService.run(shell=False)
    -> AuditLog
    -> ToolResult
    -> ExecutionResult compatibility adapter
    -> existing Kernel retry / rollback / Git behavior
```

All three test call sites use the injected session in the production CLI path. A command list remains sequential and stops at the first failure, preserving legacy behavior.

## Migration Scope

Migrated:

- TaskSpec `test_commands` preflight execution;
- TaskSpec tests after a `file_replacement` response;
- TaskSpec tests after a patch response;
- command preparation, persistent task lifecycle, authorization, approval consumption, process execution, result audit, and conversion to the existing `ExecutionResult` contract.

The runtime is created lazily after the existing clean-tree check and task branch checkout. Production `apos run` excludes `.apos/` from Git status because task, approval, and audit state are now expected runtime artifacts.

## Legacy Residual Paths

The following remain intentionally outside this migration:

- `CommandPatchCoder` process execution;
- legacy `Kernel` orchestration, patch application, retries, and rollback;
- `GitClient` subprocess operations;
- benchmark, evolution, and direct programmatic `Kernel(root)` callers;
- Ollama and local coder integration;
- every production command other than the selected TaskSpec test execution slice.

The default `Kernel` runner remains the legacy executor for non-production or not-yet-migrated callers. The production `cmd_run` adapter supplies the modern runner. The repository therefore does not yet enforce a universal privileged-operation gateway.

## Authorization Capability

The migrated command uses `Capability.TEST_EXECUTE`. The centralized local production profile is:

| Capability | Decision |
|---|---|
| `PROJECT_READ` | `ALLOW` |
| `TEST_EXECUTE` | `APPROVAL_REQUIRED` |
| Every unspecified capability | `DENY` |

The command policy trusts only the current Python interpreter. `python ...` and the exact current interpreter path resolve against that trusted set. Direct `pytest`, shells, package managers, Git, Node.js, PowerShell, project executables, and all other launch targets fail closed in this checkpoint.

Each permission request includes the canonical executable, argument digest, normalized working directory, environment digest and keys, network declaration, timeout, output limit, `shell=False`, request ID, and persistent task ID. `AuthorizationService.authorize_request` evaluates the exact request that was persisted; it does not reconstruct a different approval digest.

## Approval Policy

Test execution requires approval because it launches a trusted interpreter and therefore remains privileged even though its declared capability risk is `MEDIUM`. Persistent task semantics independently require one-time approval before `RUNNING`, including when a future static policy might otherwise return `ALLOW`.

For this local-only production adapter, invoking `apos run` is treated as an explicit local user request for each exact TaskSpec test command. The adapter records:

- subject: `USER:local-cli`;
- approver: `USER:local-cli`;
- source: `UNAUTHENTICATED_USER_REQUEST`;
- `authenticated=False`.

This creates and consumes a digest-bound persistent approval. It is not a per-command prompt, authenticated human identity, OS user proof, signature, MFA, or suitable remote approval mechanism. An external AI or remote adapter must not reuse this local adapter boundary.

If no local approval actor is supplied, the task remains `WAITING_APPROVAL` and no process starts. Existing persistent-task tests verify changed-request rejection, task binding, single-use consumption, concurrent claim exclusion, and consumed-approval rejection after restart.

## Task Lifecycle

One persistent task is created for each attempted command:

```text
CREATED
    -> QUEUED
    -> WAITING_APPROVAL
    -> APPROVED
    -> RUNNING
    -> SUCCEEDED | FAILED | CANCELLED
```

Task IDs correlate the parent TaskSpec ID, one production run, and command sequence. Raw command arguments are not persisted in the task store; the permission request stores an argument count and digest.

An authorization denial occurs after the exact approval is presented but before process start. Because the task has not entered `RUNNING`, the adapter closes that task as `CANCELLED`. A process non-zero exit, timeout, or start failure after `RUNNING` closes it as `FAILED`. A successful zero exit closes it as `SUCCEEDED`.

A task persistence failure cannot fall through to process execution. This fail-closed boundary has a dedicated regression test.

## Cancellation Status

`TestExecutionService.cancel(request_id)` delegates to the existing in-memory `ControlledExecutionService.cancel`. If cancellation reaches an active request, controlled execution performs best-effort process-tree termination and `TaskService` closes the task as `CANCELLED` after execution returns.

Production `apos run` remains synchronous and does not expose active request IDs or a separate CLI cancellation command. Cancellation authorization and its own audit boundary are not redesigned here. Cross-process cancellation, durable process ownership, and guaranteed descendant containment remain unresolved. This checkpoint must not be represented as a complete production cancellation system.

## Recovery Semantics

Runtime construction remains dependency composition only. `ProjectRuntime.create`, `create_read_only`, and `create_local_test_execution` do not mutate unrelated `RUNNING` tasks.

Recovery occurs only through:

```text
ProjectRuntime.recover_interrupted_tasks()
```

That explicit operation changes persisted `RUNNING` tasks to `RECOVERY_REQUIRED`; it does not resume their process. A production regression test creates an unrelated `RUNNING` task, runs the real CLI path, verifies it remains `RUNNING`, and then verifies only explicit recovery changes its state.

## Audit Lifecycle

Successful process execution records `test.run` events:

```text
REQUESTED -> AUTHORIZED -> STARTED -> COMPLETED
```

A non-zero process exit records:

```text
REQUESTED -> AUTHORIZED -> STARTED -> FAILED
```

A policy denial records:

```text
REQUESTED -> DENIED
```

Persistent task and approval transitions are also published through the existing transactional outbox as `task.lifecycle` and `approval.lifecycle` events. Events carry project, request, task, actor, capability, and parent-event correlation. Existing redaction and bounded-output behavior is unchanged.

The audit file remains an ordinary same-user project file. It is append-oriented through APOS APIs but is not tamper-evident or protected from a malicious subprocess.

## Failure Semantics

- Parse, command-policy, and workspace preparation failures produce a failed `ExecutionResult`; command-policy and workspace rejections use the modern denial audit path.
- Persistence failures stop before authorization or process launch.
- Missing approval returns `PERMISSION_REQUIRED` and leaves the task in `WAITING_APPROVAL`.
- Capability denial does not start the process and closes the approved task as `CANCELLED`.
- A non-zero exit preserves stdout, stderr, exit code, and `PROCESS_EXIT_NONZERO`; the task becomes `FAILED`.
- The compatibility adapter returns the existing `ExecutionResult`, so preflight failure still feeds the existing coder flow and post-change failure still feeds existing rollback/retry behavior.
- Existing CLI summary and exit behavior remains owned by `Kernel` and `cmd_run`.

## Security Residual Risks

This checkpoint does not provide:

- an OS-level sandbox or project filesystem confinement;
- hostile generated-code isolation;
- real network isolation (`NETWORK_DENIED` remains declarative only);
- protection against a trusted Python interpreter reading or changing files outside the project;
- prevention of Python child-process, shell, OS API, registry, credential, or network use;
- filesystem, executable, junction, reparse-point, or DLL-loading TOCTOU elimination;
- authenticated human approval or remote identity proof;
- tamper-evident audit storage;
- OS-enforced resource or process-count limits;
- a cross-process project lease or execution supervisor;
- guaranteed cancellation of detached descendants; or
- repository-wide removal of legacy privileged paths.

`shell=False` prevents APOS from interpreting a command string through a shell. It does not limit what the approved Python interpreter or executed test code can do with the host user's OS rights. `ControlledExecutionService` is an authorization, audit, and bounded-launch layer, not an OS sandbox.

## Regression Coverage

`tests/test_cli_test_execution_control_plane.py` verifies:

- `test_production_run_uses_persistent_control_plane_without_legacy_executor`;
- `test_post_change_test_execution_also_uses_modern_runner`;
- `test_denied_test_capability_does_not_start_process`;
- `test_nonzero_test_closes_task_and_execution_as_failed`;
- `test_run_runtime_construction_does_not_recover_unrelated_running_task`;
- `test_test_execution_waits_when_local_approval_is_not_supplied`; and
- `test_task_persistence_failure_cannot_fall_through_to_process_execution`.

The production tests invoke `main(["run", ...])`, not only service units. Guards fail if the migrated path calls `apos.kernel.run_commands` or `apos.executor.run_command`.

Relevant inherited approval and recovery tests include:

- `PersistentTaskTests.test_consumed_approval_cannot_be_reused_after_restart`;
- `PersistentTaskTests.test_approval_rejects_changed_request_and_changed_task_id`;
- `PersistentTaskTests.test_task_cannot_enter_running_without_approval_consumption`;
- `PersistentTaskTests.test_two_workers_cannot_consume_one_approval_or_start_one_task_twice`;
- `PersistentTaskTests.test_explicit_runtime_recovery_marks_running_task_without_automatic_execution`; and
- `RuntimeTaskIntegrationTests.test_runtime_execution_consumes_persistent_approval_before_process_start`.

## Verification

Completed before checkpoint creation:

- `python -m unittest discover -s tests -v`: 134 tests passed in 223.956 seconds;
- focused production/core regression run: 64 tests passed in 73.771 seconds;
- `python -m compileall src tests`: passed;
- `git diff --check`: passed with no errors;
- Python source `shell=True` search: no matches.

## Conclusion

This vertical slice demonstrates that APOS persistent tasks, exact approval binding, authorization, audit, and controlled execution can govern the real TaskSpec test process launched by production `apos run`. It does not demonstrate containment of that process, safe remote exposure, or completion of the broader production-surface migration.
