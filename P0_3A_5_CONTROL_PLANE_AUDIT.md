# Production Control-Plane Audit

**Phase:** P0-3A.5 — Production Control-Plane Convergence
**Status:** Documentation-only audit
**Baseline:** `dd55f31e1acfd1101adb732cacd3290ad021b273`
**Intended branch:** `research/p0-3a5-control-plane`
**Scope:** Production privileged-call inventory, control-plane bypass analysis, KEEP/MIGRATE/QUARANTINE/RETIRE classification, migration order, and first modern migration slice.
**Out of scope:** Any implementation, `src/` changes, `tests/` changes, dispatcher/broker/sandbox/remote/API/MCP/GUI/auth-provider work.

---

## Executive conclusion

The repository contains a coherent modern core, but the repository-wide security invariant is not yet true.

The modern path is:

```text
ProjectRuntime
    -> TaskService
        -> persistent task / approval authority
        -> ControlledExecutionService
            -> AuthorizationService
            -> AuditLog
    -> FileSystemService
        -> AuthorizationService
        -> AuditLog
```

The production path is still largely:

```text
CLI / CUI
    -> Kernel / legacy helpers / evolution
        -> direct Path/open/read_text/write_text/unlink
        -> direct subprocess.run
        -> GitClient -> direct git subprocess
        -> Ollama -> direct HTTP or direct process
```

Therefore the highest-priority problem is **control-plane bypass**, not the absence of a stronger sandbox.

The resulting risk priority is:

```text
control-plane bypass
  > absence of OS containment
  > absence of authenticated identity
  > absence of audit tamper resistance
```

The single highest-risk production bypass is:

```text
apos run
  -> cli.cmd_run
  -> Kernel.run_task
     -> legacy executor.run_commands
        -> subprocess.run
     -> CommandPatchCoder.run
        -> subprocess.run
     -> GitClient.apply_patch / reverse_patch / commit / checkout
        -> subprocess.run(["git", ...])
     -> Kernel._apply_file_replacement / rollback
        -> direct project file write / unlink
```

This path can create processes, modify project files, apply and reverse patches, change Git state, and create commits without making `ProjectRuntime`, `TaskService`, `AuthorizationService`, the persistent approval boundary, and `AuditLog` mandatory.

The self-evolution path is broader still, but it is not the first migration target because the project owner has explicitly classified self-evolution as non-core and near-QUARANTINE.

---

## Audit basis and method

This audit uses the stable baseline identified by the handoff and repository documentation:

`dd55f31e1acfd1101adb732cacd3290ad021b273`

The following baseline code and documentation were compared:

- `src/apos/cli.py`
- `src/apos/kernel.py`
- `src/apos/executor.py`
- `src/apos/coder.py`
- `src/apos/git.py`
- `src/apos/ollama.py`
- `src/apos/evolution.py`
- `src/apos/core/runtime.py`
- `src/apos/core/tasks.py`
- `src/apos/core/permissions.py`
- `src/apos/core/filesystem.py`
- `src/apos/core/execution.py`
- `src/apos/core/workspace.py`
- `src/apos/core/tools.py`
- `SECURITY_THREAT_MODEL.md`
- `P0_3A_IMPLEMENTATION.md`
- research branch `research/p0-3b-architecture` / `P0_3B_ARCHITECTURE_DECISION.md`
- handoff branch `docs/ai-handoff` / `APOS_AI_HANDOFF.md`

### Research provenance and delivery verification

The original research session inspected the source tree from GitHub at the fixed commits above because that session could not resolve `github.com` from its local Git client. The documentation-delivery session subsequently verified the findings against a writable local clone with:

- `origin` set to `https://github.com/shycan3/APOS.git`;
- local and remote `master` at the stated baseline;
- a clean working tree before branch creation;
- the intended branch created directly from the stable baseline;
- the production modules, modern core modules, tests, and Git history available locally.

The original static call-graph analysis is preserved below. No source or test file was changed while resolving the original delivery limitation.

---

## Current privileged operation inventory

Exactly **16 privileged-operation categories** were audited, matching the P0-3A.5 scope.

Legend:

- **PR** = `ProjectRuntime`
- **TS** = `TaskService`
- **AZ** = `AuthorizationService`
- **AP** = persistent approval boundary
- **AL** = `AuditLog`
- **CE** = `ControlledExecutionService`
- **YES** = path uses the boundary
- **NO** = direct/legacy bypass exists
- **PARTIAL** = modern implementation exists, but production/legacy paths can bypass it

| # | Privileged operation | Representative entry/call path | Modern boundary status | Bypass | Security consequence | Classification |
|---:|---|---|---|---|---|---|
| 1 | Filesystem read | modern: `FileSystemService.read_file`; legacy: CLI/TaskSpec/coder/evolution/direct `Path.read_text()` | PR: PARTIAL / TS: PARTIAL / AZ: PARTIAL / AP: PARTIAL / AL: PARTIAL / CE: N/A | YES | project scope, secret-name policy, request binding and audit can be skipped | KEEP modern service; MIGRATE needed production reads; QUARANTINE evolution-specific reads |
| 2 | Filesystem write | modern: `FileSystemService.write_file`; legacy: Kernel replacement/rollback, config/run/evolution metadata, `.git/info/exclude` | PARTIAL | YES | direct mutation can bypass project authorization, approval, atomic modern write lifecycle and audit | KEEP modern service; MIGRATE core writes; QUARANTINE legacy/evolution writes |
| 3 | Subprocess execution | modern: `ControlledExecutionService.run`; legacy: `executor.run_command`, `CommandPatchCoder.run`, `GitClient.run`, Ollama binary, evolution `_execute` | PARTIAL | YES | command policy, capability decision, task approval, env sanitization and audit can be skipped | KEEP CE; MIGRATE required production execution; QUARANTINE coder/evolution |
| 4 | Process creation | modern: CE `Popen`; legacy direct `subprocess.run` in executor/coder/Git/Ollama/evolution | PARTIAL | YES | process can start outside task authority and lifecycle accounting | MIGRATE production callers onto CE/TS |
| 5 | Process cancellation | `ControlledExecutionService.cancel(request_id)`; `TaskService.cancel_task` only records cancellation when execution is already stopped for RUNNING tasks | PR: YES object composition; TS/AZ/AP/AL: NO for CE cancel itself | YES | anyone holding CE + request ID can cancel without actor/capability/audit; task and process lifecycle can diverge | MIGRATE cancellation authority into task-owned authorized/audited path |
| 6 | Git read | `cli status -> GitClient.current_branch/status`; Kernel/evolution Git queries -> `GitClient.run` | PR/TS/AZ/AP/AL/CE: NO | YES | repository metadata and Git helper behavior bypass capability/audit boundary | MIGRATE only Git-read capabilities justified by core use |
| 7 | Git write | Kernel checkout/apply/reverse/commit; evolution worktree/branch operations -> `GitClient.run` | all NO | YES | branch/index/worktree/repository state can change outside modern approval and audit | QUARANTINE current legacy Git-write automation; reconsider later as explicit capabilities |
| 8 | Git reset | `Capability.GIT_RESET` exists, but no active production `GitClient` reset path was found at baseline | capability vocabulary only | No current production implementation found | future implementation could become a high-impact bypass if added directly | Do not implement now; retain only as reserved policy vocabulary if useful |
| 9 | Git push | `Capability.GIT_PUSH` exists, but no production push implementation was found; README says promotion/merge/tag remains manual | capability vocabulary only | No current production implementation found | remote repository mutation would add high-impact remote side effects | Do not implement now; outside P0-3A.5 |
| 10 | Ollama / local model invocation | `apos refine` / configured coder -> Ollama HTTP `/api/generate` or `ollama run`; Kernel -> configured local coder process | all modern boundaries: NO | YES | local process/network access and model-directed behavior occur outside task authority | QUARANTINE |
| 11 | Network access | modern CE can request `NETWORK_ACCESS`, but enforcement is declarative; Ollama HTTP directly uses network | AZ: PARTIAL decision only; OS enforcement: NO | YES | declared deny does not stop sockets; direct Ollama HTTP bypasses even the decision path | KEEP capability concept; MIGRATE any retained network callers; QUARANTINE Ollama path |
| 12 | Environment variable access | modern `EnvironmentSanitizer` constructs reduced child env; evolution copies `os.environ`; config supports environment-driven coder settings | PARTIAL | YES | broad host environment/secret/config inheritance can bypass intended sanitization | MIGRATE to explicit allowlisted config/env boundary |
| 13 | Secret access | default `SecretPolicy` denies `.apos`, `.git`, `.ssh`, `.gnupg`, `.env`, common credential/key names via modern FS; no positive production secret service found | modern deny boundary: YES; repo-wide process boundary: NO | YES indirectly through legacy code/child processes | host credentials remain reachable by legacy Python/processes despite modern filename policy | KEEP deny-by-default concept; do not add positive secret API in MVP |
| 14 | Patch application | `apos run -> Kernel -> GitClient.apply_patch -> git apply` | all modern boundaries: NO | YES | project mutation and Git behavior occur without modern task/approval/audit | MIGRATE the *capability* later; quarantine legacy implementation until then |
| 15 | Rollback | patch: `GitClient.reverse_patch`; file replacement: Kernel direct restore/write/unlink | all modern boundaries: NO | YES | recovery itself can mutate/deletes files outside modern authority; incomplete recovery cannot be uniformly audited | MIGRATE recovery concept; retire legacy mechanism after replacement |
| 16 | Self-evolution execution | CLI/CUI -> evolution create/run/evaluate/review -> Git/worktree/files/process/env; candidate run -> Kernel | all modern boundaries: NO | YES | broad compound bypass spanning process, Git, filesystem, environment and local coder paths | QUARANTINE |

---

## Production entry points

There is no production API/MCP/remote adapter in the baseline. The primary production surface is the `apos` CLI/CUI.

### 1. Initialization and configuration

Representative commands:

```text
apos init
apos bootstrap
apos connect
apos connect-ollama
```

Effects include project-memory/configuration writes and local-coder/Ollama configuration. These handlers do not establish a mandatory `ProjectRuntime` control path.

### 2. Inspection/status

Representative commands:

```text
apos status
apos validate <taskspec>
apos task-template
```

`status` performs Git reads through legacy `GitClient`. `validate` reads a task specification through legacy data-loading code. These are useful low-risk production surfaces for convergence work because they do not need project mutation.

### 3. Task authoring/refinement

Representative commands:

```text
apos draft ...
apos refine ...
```

`draft` creates/writes a TaskSpec. `refine` reads a TaskSpec and can invoke Ollama, then writes the refined result.

### 4. Main task execution

```text
apos run <taskspec>
```

This is the most important legacy privileged production path. `cmd_run` directly instantiates/calls `Kernel`, not `ProjectRuntime`.

### 5. Run inspection/reporting

```text
apos runs list
apos runs show ...
apos report ...
```

These primarily read APOS run artifacts under `.apos`. The modern default `SecretPolicy` deliberately denies `.apos` through the generic project filesystem API, so future migration should not simply route internal state through ordinary `FileSystemService`; an internal state/reporting boundary should remain distinct from AI-visible project reads.

### 6. Benchmarking

```text
apos benchmark validate
apos benchmark show
apos benchmark run
apos benchmark compare
apos benchmark results list/show
```

Read-only benchmark inspection is low risk. Benchmark execution eventually reaches privileged task execution and therefore inherits legacy bypasses where it relies on the Kernel/runtime loop.

### 7. Self-evolution

```text
apos
apos evolve
apos evolution validate/status/create/run/evaluate/review
```

The default TTY CUI is self-evolution oriented. It invokes evolution workflows that use direct filesystem, Git and process operations, including candidate worktrees and Kernel execution.

### 8. Ollama/local coder adapter

Ollama can be reached through CLI configuration/refine flows and the local-coder protocol. The adapter can use direct HTTP and a binary fallback, outside the modern control plane.

---

## Modern control plane

### Composition root

`ProjectRuntime.create()` composes:

```text
ProjectWorkspace
AuditLog
SQLiteTaskRepository
TaskService
PermissionEngine
AuthorizationService
FileSystemService
ControlledExecutionService
ToolRegistry
```

`TaskService` is exposed as the official task interaction surface while the repository remains a private-by-convention dependency.

### Modern filesystem path

```text
caller
  -> ProjectRuntime.filesystem
     -> ProjectWorkspace.resolve / SecretPolicy
     -> AuthorizationService.authorize
     -> AuditLog lifecycle
     -> filesystem operation
```

This path meaningfully enforces project-relative logical boundaries for cooperative callers.

### Modern command-task path

```text
caller
  -> ProjectRuntime.tasks
     -> TaskService
        -> persisted task + canonical permission request
        -> persisted one-time approval
        -> atomic APPROVED -> RUNNING claim
        -> ControlledExecutionService.run
           -> working-directory validation
           -> command assessment
           -> AuthorizationService
           -> optional NETWORK_ACCESS authorization decision
           -> environment sanitization
           -> process launch
           -> execution audit
        -> task terminal transition / recovery state
        -> audit outbox publication
```

This is the strongest existing end-to-end path.

### Important caveats

1. No production CLI path instantiates `ProjectRuntime` at the baseline.
2. `ControlledExecutionService` is a controlled host wrapper, not an OS sandbox.
3. `ControlledExecutionService.cancel()` is not actor/capability authorized or independently audited.
4. `NetworkPolicy` reports only a declarative decision; it does not deny sockets at the OS layer.
5. `ToolRegistry` is metadata only, not a dispatcher or security boundary.
6. Modern project filesystem APIs intentionally deny `.apos` and `.git`; internal APOS state and Git need separate controlled abstractions rather than bypassing those restrictions.

---

## Legacy execution paths

### Main task path

```text
apos run
  -> cli.cmd_run
  -> Kernel.run_task
     -> GitClient.ensure_repo / status / checkout_branch / exclude_path
     -> legacy executor.run_commands     # preflight/test
        -> subprocess.run(..., shell=False)
     -> CommandPatchCoder.run
        -> direct subprocess.run
        -> direct project-context reads
     -> legacy PermissionManager
     -> one of:
        -> GitClient.apply_patch
        -> Kernel._apply_file_replacement -> Path.write_text
     -> legacy executor.run_commands     # verification
     -> on failure:
        -> GitClient.reverse_patch
        -> or direct file restore/unlink
     -> on success:
        -> GitClient.commit
```

Modern services are not mandatory anywhere in this flow.

### Git client

```text
GitClient.run
  -> subprocess.run(["git", *args], cwd=project_root)
```

This is a generic Git escape hatch. Read and write operations share the same low-level primitive.

### Local coder

```text
Kernel
  -> CommandPatchCoder.run
     -> collect project context via direct file reads
     -> subprocess.run(configured coder command)
```

The configured coder is not launched through `ControlledExecutionService`.

### Ollama

```text
refine / local model path
  -> Ollama HTTP request to /api/generate
  -> fallback: subprocess.run([ollama_binary, "run", model])
```

Both paths bypass modern task authority.

### Evolution

```text
CLI / CUI
  -> evolution.create_candidate
     -> Git worktree / branch through GitClient
     -> metadata writes
  -> evolution.run_candidate
     -> Kernel.run_task
  -> evolution.evaluate_candidate
     -> direct subprocess execution
     -> Git reads
     -> report/metadata writes
  -> evolution review/state
     -> direct JSON/Markdown reads/writes
```

Evolution also constructs a child environment from the host environment, making it a compound privileged bypass.

---

## Bypass paths

The following repository paths can perform privileged operations without making the modern control plane mandatory:

1. `cli.cmd_run -> Kernel`
2. `Kernel -> executor.run_command(s) -> subprocess.run`
3. `Kernel -> CommandPatchCoder.run -> subprocess.run`
4. `Kernel -> CommandPatchCoder._collect_file_context -> direct file reads`
5. `Kernel -> _apply_file_replacement -> direct file write`
6. `Kernel rollback -> direct write/unlink`
7. `Kernel -> GitClient.apply_patch/reverse_patch/commit/checkout`
8. `GitClient.run -> direct git subprocess`
9. `GitClient.exclude_path -> direct .git/info/exclude read/write`
10. `Ollama HTTP adapter -> direct network`
11. `Ollama binary adapter -> direct subprocess`
12. `evolution._execute -> direct subprocess`
13. `evolution -> GitClient worktree/branch/status/diff operations`
14. `evolution -> direct JSON/Markdown metadata and report writes`
15. `evolution -> full/near-full host environment copy`
16. CUI/orchestrator -> evolution/proposal/state legacy calls
17. `ControlledExecutionService.cancel(request_id)` -> process termination without `AuthorizationService` or its own audit lifecycle

The first sixteen are legacy/production bypass families. Item 17 is a modern-core authority gap.

---

## Call graph analysis

### A. `apos run` — highest priority

```text
Production CLI
  -> Kernel
     +-> GitClient ------------------------------+
     |    +-> subprocess.run(["git", ...])       |
     |                                           |
     +-> executor.run_commands ------------------+--> host OS/process/Git
     |    +-> subprocess.run(...)                |
     |                                           |
     +-> CommandPatchCoder ----------------------+
     |    +-> direct project reads               |
     |    +-> subprocess.run(...)                |
     |                                           |
     +-> direct Path.write_text / unlink --------+--> project filesystem
```

Security properties skipped:

- no mandatory `ProjectRuntime`;
- no persistent task identity;
- no persistent approval consumption;
- no centralized `AuthorizationService`;
- no modern capability decision for each operation;
- no correlated modern audit lifecycle;
- no controlled process registry/cancellation ownership.

### B. modern command task

```text
ProjectRuntime
  -> TaskService
     -> persistent request + approval
     -> ControlledExecutionService
        -> AuthorizationService
        -> AuditLog
        -> Popen(shell=False)
     -> persistent task result
```

This is the pattern that production adapters should converge toward.

### C. self-evolution

```text
CLI/CUI
  -> evolution
     +-> GitClient -> git process
     +-> Kernel -> legacy execution/write/Git
     +-> _execute -> subprocess.run
     +-> direct metadata/report filesystem
     +-> os.environ-derived child environment
```

Because it composes multiple bypasses, it should not be used as a migration proving ground.

---

## Authority boundary analysis

The modern architecture has a meaningful distinction between:

- policy decision;
- task authority;
- human approval intent;
- execution;
- audit.

However, repository-wide enforcement remains voluntary because production adapters can call legacy primitives directly.

### Task authority

P0-3A materially improves authority for persistent command tasks:

- canonical permission request persistence;
- one-time approval consumption;
- atomic `APPROVED -> RUNNING`;
- restart replay protection;
- `RECOVERY_REQUIRED` for persisted running tasks after restart.

This authority is only effective when the caller uses `TaskService`.

### Human identity

Approval semantics are stronger than before, but `USER` is still a caller-constructed actor label. There is no authenticated identity provider. This is intentionally deferred and is not a P0-3A.5 implementation target.

### Cancellation

Cancellation is not fully under the same authority boundary:

- `TaskService.cancel_task()` governs task state;
- `ControlledExecutionService.cancel(request_id)` governs a process;
- the process cancellation method itself has no actor/capability authorization and no dedicated audit event;
- a running task cannot simply be marked cancelled unless execution is known to be stopped.

Therefore task cancellation and process cancellation should be converged later into one task-owned operation.

### Git authority

Git capabilities exist in the policy vocabulary, but there is no modern Git service. Legacy GitClient is a direct subprocess wrapper. This means “capability exists” must not be mistaken for “capability is enforced.”

---

## Security consequences

### 1. Project-boundary bypass

Direct `Path` and child processes do not have to call `ProjectWorkspace.resolve()`. Generated or legacy code can therefore access files outside the logical project boundary using the host user's OS rights.

### 2. Secret-policy bypass

The modern secret-name policy applies to cooperative `FileSystemService` operations. Legacy Python and child processes can read `.env`, SSH material, credential stores, user-profile files, agents or other secrets if OS permissions allow them.

### 3. Approval bypass

A legacy call can execute or write without a persistent task/approval. Therefore P0-3A approval replay protection is not a repository-wide guarantee.

### 4. Audit incompleteness

Modern file/execution methods emit correlated events, but legacy Git/process/filesystem/Ollama/evolution paths do not. An audit record cannot currently be treated as a complete ledger of all APOS-caused privileged activity.

### 5. Cancellation/lifecycle split

The modern execution object can be cancelled without authorization while the task state is separately governed. This creates inconsistent authority and recovery semantics.

### 6. Network non-enforcement

Even modern `NETWORK_DENIED` is declarative, not OS-enforced. Direct Ollama HTTP also bypasses the capability decision. Network containment must not be claimed at MVP.

### 7. Process containment gap

Even authorized modern processes inherit the host user token. `cwd`, `shell=False`, allowlisted executable selection, timeout and environment reduction are useful controls but not a filesystem/network/process security boundary.

### 8. Evolution multiplies bypasses

Self-evolution combines worktree/Git mutation, subprocesses, Kernel execution, direct files and environment access. Migrating it now would enlarge scope without proving the core production path.

---

## KEEP classification

KEEP means the component or concept directly supports the new APOS identity and should remain part of the modern foundation.

### Modern core

- `ProjectRuntime` as the project-scoped composition root.
- `ProjectWorkspace` and project-relative path policy.
- default `SecretPolicy` deny behavior.
- `PermissionEngine`.
- `AuthorizationService`.
- capability/request/digest model.
- `AuditLog` as current audit infrastructure, while acknowledging its non-tamper-proof status.
- `FileSystemService`.
- `ControlledExecutionService.run` as the current cooperative host execution wrapper.
- `TaskService`.
- persistent SQLite task/approval repository and recovery semantics.
- one-time persistent approval consumption.
- task audit outbox/replay mechanism.
- `ToolRegistry` **only as metadata/schema registry**, not as a dispatcher or security boundary.
- structured errors/results.
- the CLI **as a product adapter concept**, provided handlers are migrated behind modern services.

### KEEP as policy vocabulary, not active product features

- `NETWORK_ACCESS` capability.
- `SECRET_ACCESS` capability as a reserved/deny-by-default concept.
- Git capability vocabulary, where useful for future explicit authorization.

Keeping a capability enum does not authorize implementing or exposing that capability now.

---

## MIGRATE classification

MIGRATE means the local capability remains valuable, but its existing privileged implementation cannot remain an official path.

### Highest priority

1. A small read-only CLI path, selected below as the first migration slice.
2. Production task/test execution that is still implemented through legacy `executor`.
3. Process creation under task ownership.
4. Process cancellation into an authorized, audited, task-owned lifecycle.
5. Required project reads/writes into explicit project-scoped modern services.
6. Patch application as a structured, explicitly authorized project mutation if APOS continues to accept changes from a high-level AI.
7. Rollback/recovery as an explicit modern recovery capability, rather than Kernel-specific reverse operations.
8. Required Git **read** operations such as repository status/diff/identity, if the product still needs them.
9. Environment usage into a deliberately constructed/allowlisted boundary.
10. Any future retained network access into explicit capability decisions; actual network isolation remains a later containment phase.

### Important non-migration by default

Existing automatic Git branch/commit/worktree behavior is not automatically in MIGRATE. It must first re-justify itself against the new APOS identity.

---

## QUARANTINE classification

QUARANTINE means do not delete immediately, but remove from the official privileged path and do not build new modern features on top of it.

- `Kernel` as the current legacy orchestration engine.
- legacy `PermissionManager`.
- `CommandPatchCoder`.
- configured local-coder execution path.
- Ollama HTTP adapter.
- Ollama binary adapter.
- `apos refine` behavior that depends on local Ollama.
- automatic legacy Git write workflow:
  - task branch checkout;
  - `git apply`;
  - reverse patch;
  - automatic commit;
  - evolution worktree/branch creation.
- self-evolution subsystem.
- default self-evolution CUI/orchestrator.
- evolution proposal/candidate/evaluate/review direct filesystem/process/Git paths.
- benchmark **execution** paths that transitively depend on legacy Kernel execution, until the underlying execution path is migrated.

Read-only reporting/benchmark comparison logic can be separately retained if it remains valuable and can be made non-privileged or routed through controlled internal-state access.

---

## RETIRE classification

No existing subsystem should be physically deleted during P0-3A.5. This phase is documentation-only.

The following are **retirement targets/candidates after their replacements or quarantine decisions are proven**:

1. `executor.run_command` / `executor.run_commands` as a generic direct-subprocess primitive once all justified execution has moved to modern controlled execution.
2. “automatic local coder -> task branch -> patch -> test -> auto-commit” as a required **core product behavior**. It may remain temporarily for legacy compatibility, but it no longer defines APOS.
3. Any planned production implementation of Git reset or Git push is retired from the current roadmap. No baseline production implementation was found, and P0-3A.5 must not add one.
4. Evolution-only CUI affordances become retirement candidates if self-evolution is eventually retired rather than merely quarantined.

This intentionally avoids deleting code merely because it is legacy.

---

## Recommended migration order

### Step 0 — Freeze the invariant

Adopt this repository-wide target before adding new execution surfaces:

> No production adapter may perform project filesystem mutation, process creation, process cancellation, Git mutation, local-model execution, or network access except through an explicitly designated modern authority path.

P0-3A.5 does not implement this invariant; it defines the migration target.

### Step 1 — First Modern Migration Slice: read-only TaskSpec validation

Migrate one existing, low-risk production adapter: `apos validate <taskspec>`.

Target:

```text
apos validate
  -> ProjectRuntime
  -> TaskService
  -> canonical PROJECT_READ request
  -> persistent task/approval authority as selected for the slice
  -> FileSystemService.read_file
  -> AuthorizationService
  -> AuditLog
  -> TaskSpec parse/validate
  -> structured result
```

No process, Git write, project write, or network access is required.

### Step 2 — Read-only production inspection

After the first slice proves runtime composition and adapter wiring:

- migrate project-visible read-only inspections that fit `FileSystemService`;
- design a separate internal-state reader for `.apos` artifacts instead of weakening the default secret policy;
- migrate only the Git-read operations that APOS genuinely needs.

### Step 3 — Test/process execution

Move deterministic local test execution from legacy `executor` to:

```text
Production adapter
  -> TaskService
  -> ControlledExecutionService
  -> AuthorizationService
  -> audit/result
```

Do not add an ExecutionBroker or Job Object in this step. First prove all production callers use the existing modern service.

### Step 4 — Cancellation convergence

Make process stop/cancel a single task-owned authority operation in the future design:

```text
authorized actor
  -> TaskService cancellation operation
  -> execution stop
  -> verified stop result
  -> task state transition
  -> audit
```

This removes the current split between unaudited CE cancellation and persistent task state.

### Step 5 — Project mutation

Migrate only mutations that remain justified by APOS's local-capability identity:

- explicit file writes;
- structured patch application;
- verified rollback/recovery.

Do not recreate the entire Kernel inside the modern core.

### Step 6 — Git decision

Split Git by capability:

- Git read: migrate only if required.
- Git write/commit/worktree: keep quarantined until an explicit product use case justifies each operation.
- Git reset/push: do not add.

### Step 7 — Remove official legacy execution access

Once equivalent required capabilities exist behind the modern path:

- stop production adapters from calling `Kernel`, generic `executor`, direct Git write helpers, coder processes and Ollama directly;
- legacy modules may remain for compatibility/testing, but they are no longer official privileged entry points.

### Step 8 — Reassess P0-3B

Only after privileged production paths converge should APOS resume:

- mandatory execution broker design;
- manifests;
- Job Object host hardening;
- staged/container isolation.

This is consistent with the P0-3B research finding that sandboxing beside bypass paths would create a false security boundary.

---

## First migration candidate

### Candidate

**Existing command: `apos validate <taskspec>`**

### Why this candidate

It is the smallest credible vertical slice because it is:

- already a production CLI command;
- read-only;
- no Git write;
- no project write;
- no process execution;
- no remote network;
- no Ollama/local model requirement;
- naturally project-scoped;
- compatible with `ProjectWorkspace` + `FileSystemService`;
- useful for proving `ProjectRuntime` creation from a real adapter;
- useful for proving persistent task/request/audit correlation without mixing in OS containment;
- easy to compare against the existing TaskSpec validation behavior.

### Compatibility constraint

The legacy validator may accept paths without the modern project boundary. The migrated slice should deliberately define a project-scoped contract. An outside-project TaskSpec should not silently bypass `ProjectWorkspace`.

### Approval design note

P0-3A's persistent task flow currently treats persistent task approval separately from static capability ALLOW. The first slice should explicitly decide whether a read-only validation task is:

- a persisted task with explicit one-time approval for the purpose of proving the full P0-3A path; or
- a persisted/recorded read task permitted by a future lower-friction read policy.

For the **first proving slice**, explicit approval is acceptable because the goal is architecture validation, not final UX optimization.

### What this slice must prove

1. CLI does not instantiate or call legacy privileged helpers.
2. One `ProjectRuntime` owns the request.
3. A task ID correlates the operation.
4. The read request is canonical and project-relative.
5. Authorization is evaluated.
6. Secret/path policy is applied.
7. Approval semantics are explicit.
8. Audit records correlate request/decision/lifecycle.
9. The returned validation result is structured.
10. No project file, Git state, process or network state is changed.

### What this slice must not include

- new dispatcher;
- ExecutionBroker;
- Job Object;
- sandbox/container;
- Git integration;
- local model;
- remote adapter;
- GUI;
- refactor of the full CLI;
- Kernel migration.

---

## Handoff and repository differences

### 1. `TaskService` wording requires precision

The handoff's statement that `TaskService` is the “official control plane” is accurate **for the modern architecture**.

It is not yet accurate as a repository-wide mandatory-enforcement claim.

More precise wording:

> `TaskService` is the intended and officially exposed task control plane of the modern core, but production APOS 1.x adapters still contain privileged paths that do not enter it.

### 2. Security documentation is unusually aligned with the code

`SECURITY_THREAT_MODEL.md` does not claim repository-wide authorization. It explicitly records that the invariant “every privileged operation passes through AuthorizationService” is false for the repository as a whole and lists GitClient, legacy executor, coder, Ollama, Kernel replacement/rollback, evolution and orchestrator bypasses.

Therefore the current security documentation is not the source of the architectural mismatch; the mismatch is between the modern core and the still-active legacy production surface.

### 3. P0-3B research correctly identifies bypass closure as prerequisite

The research branch is documentation-only and explicitly warns against placing a sandbox beside bypassing production paths.

Its original implementation sequence begins with closing execution bypasses before stronger isolation providers.

### 4. Owner direction refines the sequencing

The owner has now made the sequencing more conservative:

```text
P0-3A
  -> P0-3A.5 Production Control-Plane Convergence
  -> only then reconsider P0-3B implementation
```

This is not a contradiction with the handoff or P0-3B research. It is a clarification that the bypass-closure problem deserves its own investigation/migration phase before an execution broker or isolation implementation begins.

### 5. Product identity has been narrowed

Legacy README language still describes APOS 1.x heavily in terms of local coder, retry, Git commit and self-evolution workflows.

The owner-approved long-term identity is narrower and more durable:

> APOS is a project-scoped local execution and control layer that exposes controlled local capability to a high-level AI.

Therefore existing functionality must now be justified against that identity instead of preserved by default.

---

## Decision summary

### Core conclusion

The modern architecture is real, coherent, and materially stronger than the legacy runtime.

The production architecture has not converged onto it.

### Most dangerous current path

```text
apos run
  -> Kernel
  -> executor / coder / direct filesystem / GitClient
```

### Immediate architectural objective

Prove that one real production adapter can use the modern architecture end-to-end before migrating any write/execution-heavy workflow.

### First slice

```text
apos validate
  -> ProjectRuntime
  -> TaskService
  -> Authorization
  -> FileSystemService read
  -> Audit
  -> structured validation result
```

### Do not implement yet

- ExecutionBroker
- dispatcher
- Job Object
- Docker/container
- VM / Windows Sandbox / AppContainer / restricted token
- MCP
- Discord
- GUI
- remote API / relay
- authentication provider
- Local LLM expansion
- self-evolution expansion
- Git workflow expansion

---

## Delivery checklist

- [x] Audited 16 requested privileged-operation categories.
- [x] Identified production CLI/CUI entry points.
- [x] Identified modern control-plane paths.
- [x] Identified legacy and modern-core bypasses.
- [x] Identified the highest-risk production bypass.
- [x] Classified KEEP/MIGRATE/QUARANTINE/RETIRE.
- [x] Designed a First Modern Migration Slice.
- [x] Recommended migration order.
- [x] Compared handoff/security/P0-3B claims with repository state.
- [x] No APOS source implementation was produced.
- [x] No APOS tests were modified.
- [x] Baseline working-tree checkout verified by the documentation-delivery session.
- [x] Intended research branch created from the stable baseline.
- [x] Repository-native change-scope and `git diff --check` verification performed before commit.

Commit and push SHAs are delivery metadata and are reported by the delivery session after GitHub verification rather than embedded prospectively in this research document.

---

## Stop condition

P0-3A.5 investigation ends here.

No P0-3B implementation, sandbox work, remote architecture, local-model expansion, self-evolution expansion, or Git workflow expansion should begin until the owner reviews this audit and explicitly selects the implementation phase.
