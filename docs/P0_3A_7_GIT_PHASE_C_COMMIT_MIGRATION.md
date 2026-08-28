# P0-3A.7 Git Phase C Commit Migration

Phase C migrates the production `apos run` successful-change commit path into the APOS control plane. The migrated path covers the common `ControlledGitClient.commit(...)` call used after a successful patch application and after a successful file replacement.

## Migration Boundary

In scope:

- `git add -- <files>`
- `git diff --cached --quiet`
- `git commit --no-verify -m <message>`
- post-commit verification reads

Out of scope:

- benchmark, evolution, and orchestrator Git operations
- generic Git diff operations
- worktree and remote operations
- OS sandboxing, real network isolation, authenticated human identity, and backup/restore

## Capability Mapping

- `git add -- <files>` uses `GIT_INDEX_WRITE`.
- `git diff --cached --quiet` uses `GIT_READ`.
- staged status, staged diff, tree, parent, and message verification reads use `GIT_READ`.
- `git commit --no-verify -m <message>` uses `GIT_REF_WRITE`.

The local production Git runtime allows `GIT_READ` and requires approval for `GIT_WORKTREE_WRITE`, `GIT_INDEX_WRITE`, `GIT_REF_WRITE`, and `GIT_ROLLBACK`.

## Approval Binding

Mutation requests include semantic metadata in the permission request digest. Commit approval is bound to the operation, branch, pre-commit HEAD, expected changed files, patch digest when available, staged diff digest, staged tree identity, commit message digest, task id, and attempt number when available.

Changing the staged semantic state or commit message changes the permission request digest.

Immediately before `git commit` starts, APOS revalidates the live branch, HEAD, staged files, staged diff digest, and index tree against the approved semantic state. If the live state differs, the commit process is not started and the command fails before ref mutation. If the live state cannot be verified, the commit process is not started and recovery is required.

## Staged Change Policy

Phase C rejects pre-existing staged changes before APOS begins index mutation. This avoids accidentally including unrelated user-staged changes in the APOS commit, especially when `apos run --allow-dirty` is used.

## Ambiguous State Policy

If `git add` fails and the index is unchanged, the operation fails normally. If the index changed or cannot be verified, APOS raises a recovery-required Git error.

`git diff --cached --quiet` is interpreted exactly:

- exit code `0`: no staged differences, fail without committing
- exit code `1`: staged differences exist, continue
- any other nonzero code: command failure; recovery is required if state changed or cannot be verified

If `git commit` fails with unchanged HEAD and unchanged index, the operation fails normally. If HEAD changed, the index changed unexpectedly, or state cannot be verified, recovery is required.

If commit succeeds but post-commit verification fails, recovery is required.

## Post-Commit Verification

After a successful commit process result, APOS verifies:

- branch remained unchanged
- HEAD changed
- new commit parent equals the approved pre-commit HEAD
- new commit tree equals the approved staged tree
- commit message equals the generated APOS message
- expected staged entries are cleared

Full commit IDs are used internally for verification. The returned commit hash remains a short presentation value.

## Hook Safety

The migrated commit path executes through `ControlledExecutionService` with the existing trusted Git executable, sanitized environment, and Git safety configuration. It also passes `--no-verify` to suppress commit hooks on the production APOS commit path.

## Retry Safety

Kernel converts controlled commit ambiguity into a `RECOVERY_REQUIRED` run summary and does not start another model attempt. Ordinary verified commit failures become `FAILED`.
