# APOS P0-2 Implementation Report

Date: 2026-08-27

Status: P0-2 core implementation complete in the working tree

## 1. Architecture Changes

P0-2 establishes this enforced path for the new APOS core:

```text
Adapter intent
  -> ProjectRuntime
  -> ProjectWorkspace boundary
  -> AuthorizationService
  -> PermissionEngine
  -> append-only AuditLog
  -> FileSystemService or ControlledExecutionService
  -> bounded ToolResult
```

`ProjectRuntime` is the composition root for future CLI, MCP, API, and GUI
adapters. It requires explicit permission and command policies; there is no
default allow policy.

The pre-existing Local Coder runtime remains for compatibility and is not a
dependency of the new core. Its three string-command subprocess paths were changed
to parsed argv with `shell=False` so shell interpretation is no longer present in
the repository.

## 2. Files Added

- `src/apos/core/audit.py`
- `src/apos/core/commandline.py`
- `src/apos/core/execution.py`
- `src/apos/core/permissions.py`
- `src/apos/core/runtime.py`
- `src/apos/core/tools.py`
- `tests/test_core_execution.py`
- `tests/test_core_runtime.py`
- `tests/test_core_security.py`
- `P0_2_IMPLEMENTATION.md`

## 3. Files Modified

- `src/apos/core/__init__.py`
- `src/apos/core/filesystem.py`
- `src/apos/core/result.py`
- `src/apos/coder.py`
- `src/apos/evolution.py`
- `src/apos/executor.py`
- `tests/test_core_filesystem.py`
- `.gitignore`

Other modified and untracked APOS 1.2 CUI draft files predate P0-2 and remain
preserved as unresolved working-tree changes.

## 4. Files Deleted

None.

## 5. Permission Model

Capabilities:

```text
PROJECT_READ       PROJECT_WRITE      PROJECT_DELETE
PROCESS_EXECUTE    TEST_EXECUTE       LOCAL_LLM_EXECUTE
GIT_READ           GIT_WRITE          GIT_RESET          GIT_PUSH
NETWORK_ACCESS     SECRET_ACCESS
```

Actors are provider-neutral:

```text
USER  EXTERNAL_AI  LOCAL_LLM  SYSTEM
```

Every `PermissionRequest` binds:

- request and project ID
- actor
- capability
- resource and operation
- risk level
- task ID
- security-relevant metadata

The decision is exactly one of `ALLOW`, `DENY`, or `APPROVAL_REQUIRED`. Missing
rules deny by default. Policy exceptions and malformed policy responses fail
closed with `POLICY_EVALUATION_FAILED`.

Approval grants:

- may be issued only by `USER` or `SYSTEM`
- are bound to project ID, request ID, and the SHA-256 digest of the complete
  permission request
- bind file content by digest and commands by args/environment digests
- are consumed once and cannot be replayed
- do not treat External AI intent as authorization

## 6. Audit Model

Audit events are append-only JSON Lines records under:

```text
.apos/audit/events.jsonl
```

Lifecycle states:

```text
REQUESTED
AUTHORIZED | DENIED | APPROVAL_REQUIRED
STARTED
COMPLETED | FAILED | CANCELLED
```

Events contain event, request, task, project, and parent-event IDs; actor,
operation, capability, resource, decision, status, duration, exit code, error code,
changed paths, and redacted metadata.

The store uses process-local serialization, append, flush, and `fsync`. Audit data
is distinct from debug output and is excluded from Git tracking.

Redaction covers:

- sensitive metadata keys
- token/password/API-key command forms
- bearer credentials
- secret environment assignments
- private-key blocks
- known secret values collected from sensitive host environment variables
- caller-supplied environment values

Raw file content and raw command output are not stored in audit metadata.

## 7. Execution Model

`CommandRequest` uses an executable plus an argument tuple. Shell strings are not
accepted by the controlled runtime.

Execution properties:

- exact trusted executable resolution
- no project-directory PATH lookup
- `shell=False`
- canonical project-root or project-child working directory
- junction and symlink escape rejection
- minimal child environment instead of parent environment inheritance
- dangerous loader and path environment overrides denied
- per-request timeout
- external cancellation by request ID
- Windows process-tree termination with `taskkill /T /F`
- POSIX process-group termination
- concurrent execution lock per `ProjectRuntime` instance
- continuously drained, bounded stdout and stderr
- redacted output in structured results
- separate network capability request
- machine-readable failures and audit correlation

Legacy string commands are parsed only for compatibility and are executed as argv
without a shell.

## 8. Security Assumptions

- Permission and command policies are trusted local configuration.
- Future adapters must receive a `ProjectRuntime` and must not instantiate OS
  process or filesystem primitives directly.
- The trusted executable list contains binaries selected by the user/controller.
- `USER` and `SYSTEM` approval identities are supplied by a future trusted adapter;
  P0-2 defines the binding but does not authenticate an operating-system user.
- Process output, source files, and repository documents are untrusted data.
- `NETWORK_DENIED` is currently a declared policy state, not an OS firewall.

## 9. Known Limitations

- There is no OS-level filesystem sandbox. An explicitly authorized general-purpose
  interpreter can still access host paths using its own APIs. P0-2 prevents default
  authorization and reports this limitation; it does not claim containment that the
  operating system does not enforce.
- Network policy is `DECLARATIVE_ONLY`; firewall or container isolation is pending.
- Memory and process-count limits exist in the request model but are reported as
  unenforced.
- The project execution lock is process-local, not cross-process.
- JSONL audit writes are fsynced but are not cryptographically chained,
  tamper-evident, or coordinated across multiple APOS processes.
- Approval grants have exact digest binding and one-time consumption but no expiry.
- The legacy TaskSpec/Local Coder CLI has not yet been rebuilt on `ProjectRuntime`.
  It must not be exposed as the future external-AI bridge.
- CUI approval identity is still not trusted and the CUI remains an unreleased draft.

## 10. Tests Added

Security tests cover:

- explicit allow and default deny
- approval required
- policy failure fail-closed behavior
- AI actor approval rejection
- exact request/content/args/environment digest binding
- one-time approval consumption
- authorized, denied, pending, failed, and cancelled audit events
- request/task/event correlation
- audit and output redaction
- shell metacharacters treated as argv data
- shell-syntax executable rejection
- project-local PATH hijacking rejection
- absolute and junction cwd escape rejection
- environment sanitization and loader-variable rejection
- explicit package-install network denial
- separate network approval
- timeout and child-process-tree termination
- external cancellation
- bounded large output
- shared runtime composition and tool metadata

P0-1 traversal, absolute-path, secret, symlink, junction, and atomic-write tests
remain active through the authorized filesystem service.

## 11. Test Results

Before the final documentation update:

- 92 tests passed in 215.007 seconds after removing all `shell=True` paths
- 31 focused P0 core tests passed in 8.752 seconds after exact digest binding
- `python -m compileall -q src tests` passed
- `git diff --check` passed
- repository search found zero `shell=True` occurrences

Final verification after the report and all exact-binding changes:

- 94 tests passed in 294.046 seconds
- no tests were skipped

## 12. Remaining Risks

The dominant remaining risk is that process authorization is not process
containment. Command allowlisting, project cwd, sanitized environment, timeout, and
redaction reduce exposure but cannot stop an allowed Python process from opening an
arbitrary host path or socket.

The second risk is adapter trust. Until approval authentication and an external tool
adapter exist, the core model can distinguish actors and bind decisions but cannot
prove that a claimed `USER` identity came from the operating-system user.

The third risk is compatibility code. Legacy CLI, benchmark, and self-evolution
paths are no longer shell-interpreted, but they do not yet share the central P0-2
permission and audit lifecycle.

## 13. Recommended P0-3

P0-3 should build the first real external bridge over `ProjectRuntime`:

1. Add a persistent task and pending-approval store.
2. Authenticate local human approval through a trusted controller boundary.
3. Expose the existing tool registry through a provider-neutral MCP adapter.
4. Add project-scoped read-only Git status/diff/log capabilities.
5. Add Git checkpoints and rollback before mutating tools.
6. Introduce cross-process project locking.
7. Select and implement a Windows process/filesystem/network isolation strategy
   before treating general-purpose interpreter execution as project-contained.
8. Migrate or quarantine legacy CLI execution so no future adapter can bypass
   `ProjectRuntime`.

P0-3 should not add autonomous planning, GUI redesign, multi-agent behavior, or
model routing.
