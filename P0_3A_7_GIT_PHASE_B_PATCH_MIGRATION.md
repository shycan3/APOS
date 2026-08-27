# P0-3A.7 Git Phase B Patch Migration

## Baseline

- Baseline master SHA: `3ec3b6c2fa8a90e029a435bd0fad8bfe348da34d`
- Working branch: `review/p0-3a7-git-phase-b`
- Scope: production `apos run` patch apply and reverse/rollback Git execution.

## Production Call Graph Before

```text
apos run
  -> Kernel
  -> ControlledGitClient.apply_patch / reverse_patch
  -> legacy GitClient.apply_patch / reverse_patch
  -> subprocess.run(["git", "apply", ...])
```

## Production Call Graph After

```text
apos run
  -> Kernel
  -> ControlledGitClient.apply_patch / reverse_patch
  -> GitExecutionSession.apply_patch / reverse_patch
  -> GitExecutionService
  -> TaskService
  -> AuthorizationService / PermissionEngine
  -> ControlledExecutionService
  -> git process with shell=False and patch over stdin
  -> repository snapshot verification
  -> AuditLog
```

## Operations Migrated

- `git apply --check -`
- `git apply -`
- `git apply --reverse --check -`
- `git apply --reverse -`

Only the production `apos run` patch path is migrated. Legacy `GitClient` remains for non-production and out-of-scope paths.

## Capabilities

- `git apply --check -`: `GIT_READ`
- `git apply -`: `GIT_WORKTREE_WRITE`
- `git apply --reverse --check -`: `GIT_READ`
- `git apply --reverse -`: `GIT_ROLLBACK`

Phase B does not use `GIT_INDEX_WRITE` or `GIT_REF_WRITE` because the migrated commands do not stage files, update refs, or create commits.

## Approval Model

Patch checks are read-only and do not require human approval.

Patch apply and reverse apply create persistent command tasks and require local user approval. The approval is bound to the exact permission request digest. The permission request metadata includes:

- operation
- patch digest
- target branch
- HEAD before mutation
- expected changed paths
- repository snapshot before mutation
- stdin digest from the exact patch text sent to Git

Approvals are consumed once by the existing `TaskService` and are not restored on failure.

## Patch Digest

The patch digest is:

```text
sha256(patch.encode("utf-8"))
```

The patch text is not normalized before hashing. The digest is used for task metadata, authorization metadata, audit correlation, execution stdin digest, and rollback correlation. A different patch produces a different permission request digest.

## Snapshot Model

Patch mutations capture repository state before and after execution:

- branch
- HEAD
- dirty boolean
- changed file list
- staged file list
- patch digest
- expected changed paths

This is a targeted verification model, not a filesystem backup or OS sandbox.

## Verification Logic

Patch apply is considered verified when:

- branch remains the same
- HEAD remains the same
- staged file set remains unchanged
- all expected changed paths are dirty after apply
- no unexpected dirty files appear outside the pre-existing dirty set plus expected paths

Reverse apply is considered verified when:

- branch remains the same
- HEAD remains the same
- staged file set remains unchanged
- expected changed paths are no longer dirty
- remaining dirty files are only pre-existing dirty files

If verification cannot run or does not match expectations after a possible mutation, APOS marks the task `RECOVERY_REQUIRED`.

## Ambiguous-State Policy

| Scenario | Task state | Retry behavior |
|---|---|---|
| patch check fails | no mutation task; operation fails | retry allowed |
| apply fails and repository is verified unchanged | `FAILED` | retry allowed |
| apply returns non-zero and repository changed | `RECOVERY_REQUIRED` | blocked |
| apply succeeds but verification fails | `RECOVERY_REQUIRED` | blocked |
| patch applied and verified | `SUCCEEDED` | continue |
| tests fail and reverse succeeds verified | rollback task `SUCCEEDED` | retry allowed |
| reverse check fails | operation fails before reverse mutation | retry blocked by Kernel rollback failure policy |
| reverse fails or state is unclear | `RECOVERY_REQUIRED` when mutation may be ambiguous | blocked |
| audit/persistence failure after possible mutation | `RECOVERY_REQUIRED` through task execution exception handling | blocked |

The central rule is: if repository state cannot be confidently determined after a possible mutation, APOS does not continue retrying.

## Retry Blocking

Kernel remains responsible for the attempt loop. If patch apply or rollback raises `GitAmbiguousStateError`, Kernel records an attempt with `RECOVERY_REQUIRED`, returns a `RunSummary` with `RECOVERY_REQUIRED`, and stops without starting the next attempt.

Rollback failure is no longer treated as a logged warning that still permits retry.

## Audit

Phase B Git commands run through `ControlledExecutionService`, so they produce the normal audit lifecycle:

```text
REQUESTED -> AUTHORIZED -> STARTED -> COMPLETED
```

Failures produce `FAILED`; authorization denial produces `DENIED` without process start. Audit metadata carries argument digest, stdin digest, capability, task id, and snapshot metadata through the persistent task record.

## Legacy Residual Paths

Still outside Phase B:

- `git add`
- `git commit`
- `git diff`
- benchmark Git operations
- evolution Git operations
- orchestrator Git operations
- worktree management
- remote Git operations
- file replacement rollback
- OS-level sandboxing

## Security Limitations

Implemented protections:

- no new raw `subprocess.run(["git", ...])` production patch path
- `shell=False`
- trusted Git executable resolution
- sanitized controlled execution environment
- patch stdin digesting
- capability authorization
- persistent approval for mutation operations
- before/after repository verification
- retry blocking on ambiguous repository state

Remaining limitations:

- `ControlledExecutionService` is not an OS sandbox.
- Declarative network policy is not actual network isolation.
- Git itself remains a trusted executable.
- Repository-level TOCTOU is not fully eliminated.
- APOS does not provide authenticated human identity.
- APOS does not provide filesystem backup/restore for arbitrary partial Git mutations.

## Validation

Required validation commands:

```text
python -m unittest tests.test_cli_git_control_plane -v
python -m unittest tests.test_cli_test_execution_control_plane tests.test_core_execution tests.test_core_tasks tests.test_core_runtime -v
python -m unittest discover -s tests -v
python -m compileall src tests
git diff --check
rg -n "shell=True|shell\\s*=\\s*True" src tests
```
