# P0-3A.7 Git Phase A Migration

## Baseline

- Baseline master SHA: `c88710dac7e0d169efed949a2d9a4aceb09ef0cc`
- Working branch: `review/p0-3a7-git-phase-a`
- Scope: first production `apos run` migration for Git read operations and task branch preparation.

## Previous Production Call Graph

```text
apos run
  -> cli.cmd_run
  -> Kernel
  -> GitClient.ensure_repo/status/current_branch/checkout_task_branch
  -> subprocess.run(["git", ...])
  -> TaskSpec test execution
```

## New Production Call Graph

```text
apos run
  -> cli.cmd_run
  -> Kernel(git_client_factory=_local_git_client)
  -> ControlledGitClient
  -> ProjectRuntime.create_local_git_phase_a
  -> GitExecutionService
  -> AuthorizationService / PermissionEngine
  -> ControlledExecutionService
  -> git process with shell=False
  -> AuditLog
  -> TaskService for branch preparation
  -> TaskSpec test execution
```

## Migration Scope

The migrated production `apos run` Git operations are:

- `ensure_repo`
- `current_branch`
- `status_porcelain`
- `has_commits`
- `checkout_task_branch`
- deterministic branch-name generation through the production Git adapter

These operations now execute through `GitExecutionService` and `ControlledExecutionService` in production `apos run`.

## Legacy Paths Not Migrated

The following paths intentionally remain legacy in this checkpoint:

- patch apply and reverse apply
- Git diff inspection outside the Phase A production path
- Git add and commit
- rollback operations
- benchmark, evolution, orchestrator, Ollama, and non-production Git uses
- `.git/info/exclude` maintenance for APOS runtime artifact exclusion

## Authorization Capability

- Git read operations use `GIT_READ`.
- Branch checkout and branch creation use `GIT_WORKTREE_WRITE`.
- Missing rules still fail closed through `StaticPermissionPolicy`.
- Git network-like subcommands such as `clone`, `fetch`, `pull`, `push`, `ls-remote`, `remote`, and `submodule` are classified as network-seeking when `NetworkPolicy.DENIED` is used.

## Approval Policy

- `GIT_READ` does not require human approval in the local production profile.
- `GIT_WORKTREE_WRITE` requires persistent local user approval because it can mutate the active worktree branch or create a branch ref.
- The approval is bound to the exact permission request digest and is consumed by `TaskService` before the controlled Git branch process starts.

## Task Lifecycle

Git branch preparation follows the persistent task lifecycle:

```text
CREATED
  -> QUEUED
  -> WAITING_APPROVAL
  -> APPROVED
  -> RUNNING
  -> SUCCEEDED / FAILED / RECOVERY_REQUIRED
```

`GitExecutionService` keeps branch tasks open until post-command branch verification succeeds. The default `TaskService.run_command_task` behavior remains unchanged for existing callers.

## Cancellation State

This checkpoint does not add a new Git cancellation API. `ControlledExecutionService.cancel()` remains the underlying process cancellation primitive and still requires a dedicated security review before it is exposed as a privileged Git control surface.

## Recovery Semantics

Runtime construction remains dependency composition only. It does not recover or mutate unrelated `RUNNING` tasks. Recovery remains explicit through:

```text
ProjectRuntime.recover_interrupted_tasks()
```

For branch preparation, a known pre-start or authorization failure closes as `FAILED`. If the Git process may have run but APOS cannot verify the resulting repository state, the task transitions to `RECOVERY_REQUIRED`.

## Audit Lifecycle

Git read operations produce `git.run` audit events with `GIT_READ`.

Branch preparation produces:

- task lifecycle audit events from `TaskService`
- `git.run` authorization and execution events from `ControlledExecutionService`
- `GIT_WORKTREE_WRITE` metadata with argument and environment digests rather than raw command arguments

Normal execution records `REQUESTED -> AUTHORIZED -> STARTED -> COMPLETED`. Authorization denial records `REQUESTED -> DENIED` and does not start the Git process.

## Failure Semantics

- Invalid branch names are rejected before branch checkout/create execution.
- Authorization denial records task failure and no Git process start.
- Non-zero Git process exits close branch preparation as `FAILED`.
- Unknown post-execution state transitions branch preparation to `RECOVERY_REQUIRED`.
- Existing test execution failure semantics remain owned by the P0-3A.6 test execution path.

## Git Process Controls

Migrated Git operations run through `ControlledExecutionService` with `shell=False`.

The Git command line includes local safety configuration:

- `core.hooksPath=<project>/.apos/tmp/git-hooks-disabled`
- `credential.helper=`
- `protocol.file.allow=never`

The controlled execution environment is constructed by `EnvironmentSanitizer`, so APOS does not blindly inherit caller `PATH`, `HOME`, `GIT_DIR`, `GIT_WORK_TREE`, `GIT_CONFIG`, `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_*`, or `GIT_CONFIG_VALUE_*` values. The command policy resolves Git from an explicit trusted executable set rather than the project PATH.

## Residual Risks

This checkpoint does not provide:

- OS-level sandboxing
- network isolation beyond declarative command classification
- hostile Git binary containment
- hook prevention for legacy Git paths
- malicious repository object isolation
- filesystem TOCTOU protection
- authenticated human identity
- OS-level single-owner project lease
- migration of patch, rollback, add, commit, or remote Git operations

`ControlledExecutionService` is still controlled process execution, not an OS sandbox.

## Verification

Added regression coverage for:

- production `apos run` Git read and branch preparation using the control plane
- legacy GitClient bypass prevention for migrated operations
- `GIT_READ` environment non-inheritance
- invalid branch rejection before checkout
- `GIT_WORKTREE_WRITE` authorization denial without process start
- human approval requirement for branch preparation
- recovery-required transition when post-checkout state verification fails
- checkout hook suppression for migrated branch preparation

Required validation commands:

```text
python -m unittest tests.test_cli_git_control_plane -v
python -m unittest tests.test_cli_test_execution_control_plane -v
python -m unittest discover -s tests -v
python -m compileall src tests
git diff --check
rg -n "shell=True|shell\\s*=\\s*True" src tests
```
