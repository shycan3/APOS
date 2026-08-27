# P0-3A.6 Production Surface Convergence Plan

## 1. Executive Summary

This document audits the production surface at master commit
`e9540429980080b475433952bc150a9af39346a2`. It is a planning artifact only.
No production code, test, policy, or runtime behavior is changed by this work.

The public CLI exposes 26 executable leaf commands or command modes, excluding
the `evolve` alias and parser-only parent nodes. The current classification is:

- 2 MODERN commands: `apos validate` and the non-privileged `apos task-template`.
- 11 LEGACY commands: setup/configuration, status, draft/refine, run, and run-log
  inspection/reporting paths.
- 0 MIXED commands at this baseline. A future partial migration must be labeled
  MIXED until every privileged path of that command is closed.
- 13 QUARANTINED command modes: benchmark and self-evolution surfaces.

`apos validate` is the only command that performs a privileged operation through
the modern control plane. `apos task-template` is classified MODERN only because
it is a pure, constant-to-stdout operation and therefore has no privileged
operation to mediate.

The recommended next vertical slice is the controlled test-execution portion of
`apos run`. Every invocation of TaskSpec `test_commands`, including preflight and
post-change verification, should move from `executor.run_commands` to a small
runtime-owned test service that uses `TaskService` and
`ControlledExecutionService`. The rest of `apos run` must remain explicitly
legacy during that checkpoint. The command must therefore be reported as MIXED
until Local LLM invocation, project mutation, Git mutation, and run-history writes
are separately migrated or retired.

This recommendation has higher product and security value than merely selecting
the easiest read-only command. It exercises capability enforcement, exact
approval binding, persistent task ownership, cancellation, recovery, bounded
output, environment reduction, and audit correlation on a real production path.
It does not provide an OS sandbox. A trusted Python interpreter can still read or
write outside the project, spawn children, and open sockets with the APOS host
user's authority. The slice is acceptable only as a local, trusted-user control
plane convergence step. It must not be exposed to an untrusted remote AI.

## 2. Current Master Baseline

| Item | Value |
| --- | --- |
| Repository | `https://github.com/shycan3/APOS.git` |
| Stable master | `e9540429980080b475433952bc150a9af39346a2` |
| Completed migration | `apos validate <taskspec>` |
| Test evidence at integration | 127 tests passed |
| Compile evidence | `compileall` passed |
| Source check | Python `shell=True` occurrences: 0 |

The implemented validation path is:

```text
apos validate
  -> cli.cmd_validate
  -> ProjectRuntime.create_read_only
  -> TaskSpecValidationService
  -> FileSystemService.read_file
  -> ProjectWorkspace / SecretPolicy
  -> AuthorizationService / PermissionEngine
  -> AuditLog
  -> authorized content read
  -> TaskSpec.from_mapping
  -> ToolResult
  -> existing CLI output
```

Runtime construction is dependency composition only. It does not perform task
recovery. `RUNNING -> RECOVERY_REQUIRED` occurs only through the explicit
`ProjectRuntime.recover_interrupted_tasks()` authority.

The modern core already contains useful mechanisms that are not yet connected to
most production commands:

- project-scoped filesystem services;
- explicit capability policy and authorization;
- append-oriented audit events;
- controlled process launch with `shell=False`, output bounds, timeouts, and
  best-effort process-tree termination;
- persistent task, approval, execution, cancellation, and recovery states;
- a metadata-only tool registry.

These mechanisms are not repository-wide mandatory boundaries. Legacy modules
remain directly callable and most CLI handlers still call them.

## 3. APOS Product Identity

APOS is an AI-independent, user-owned local project execution and control plane.
Its durable product value is the state and authority that remain with the user
when the planning or coding AI changes:

- project boundary and capability policy;
- permission requests and approval history;
- persistent task and recovery state;
- process execution and cancellation ownership;
- execution and audit history.

High-level AI systems should own planning, reasoning, task decomposition, major
code generation, debugging, and review. APOS should own local authority and
evidence. Local LLMs may remain optional proposal resources, but they are not the
center of the product.

The architecture priorities remain, in order:

1. Close production control-plane bypasses.
2. Add OS containment.
3. Add authenticated identity.
4. Add audit tamper resistance.

A migration may improve priority 1 without claiming priorities 2 through 4 are
complete. Each checkpoint must state that distinction plainly.

## 4. Production Surface Inventory

Risk reflects current host impact if the path is misused, not merely whether the
normal command is expected to be read-only. `Y` means the operation is reachable
on the actual production path. `Conditional` means an option or fallback enables
it. Direct Path and legacy Git operations do not pass through the modern core.

| # | Command | Purpose / entry | Main modules | FS read | FS write | Process / Git | Network / LLM | Env or secret access | Class | Risk |
| ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `apos init` | Initialize APOS memory / `cmd_init` | `cli`, `config`, `git` | Git metadata | `.apos` memory/config | Git read | No | No | LEGACY | Medium |
| 2 | `apos bootstrap` | Initialize and optionally configure Ollama / `cmd_bootstrap` | `cli`, `config`, `git` | Config, Git | `.apos` memory/config | Git read | Stores Ollama settings | No | LEGACY | Medium |
| 3 | `apos connect` | Store arbitrary coder command / `cmd_connect` | `cli`, `config`, `git` | Config, Git | `.apos/config.json` | Git read; stores future launcher | Future command may use network/LLM | Command may later reach secrets | LEGACY | High |
| 4 | `apos connect-ollama` | Store Ollama adapter settings / `cmd_connect_ollama` | `cli`, `config`, `git` | Config, Git | `.apos/config.json` | Git read | Stores host, model, binary | Host/binary configuration | LEGACY | High |
| 5 | `apos status` | Show project, branch, config, dirty state / `cmd_status` | `cli`, `config`, `git` | Config, Git | No | Direct Git subprocess | No | Displays configured command | LEGACY | Low |
| 6 | `apos validate` | Validate one TaskSpec / `cmd_validate` | `cli`, `core.runtime`, `core.validation`, `core.filesystem` | Authorized project read | Internal task/audit initialization and audit append | No | No | Secret paths denied | MODERN | Low |
| 7 | `apos task-template` | Print constant TaskSpec JSON / `cmd_task_template` | `cli` | No | No | No | No | No | MODERN (non-privileged) | Low |
| 8 | `apos draft` | Build TaskSpec and optionally save it / `cmd_draft` | `cli`, `draft`, `models`, `git` | Direct `tasks/*.json`, Git | Conditional direct output write | Git read | No | No | LEGACY | Medium |
| 9 | `apos refine` | Refine TaskSpec with Ollama / `cmd_refine` | `cli`, `draft`, `ollama`, `config`, `models`, `git` | Direct TaskSpec/config/Git | Conditional direct output write | Git read; Ollama CLI fallback | HTTP to configured host; local LLM | Configured host/binary; inherited CLI env | LEGACY | High |
| 10 | `apos run` | Legacy coding/test/commit loop / `cmd_run` | `cli`, `kernel`, `executor`, `coder`, `git`, `runlog` | Direct TaskSpec, source, config | Source replacement, patch, rollback, run logs | Test and coder subprocess; Git branch/apply/commit | Conditional coder/Ollama/network | `APOS_CODER_COMMAND`; inherited child env | LEGACY | Critical |
| 11 | `apos runs list` | List legacy run records / `cmd_runs_list` | `cli`, `runlog`, `git` | Direct `.apos/runs` and Git | No | Git read | No | Logs may contain sensitive data | LEGACY | Low |
| 12 | `apos runs show` | Show one legacy run / `cmd_runs_show` | `cli`, `runlog`, `git` | Direct run JSON and artifacts | No | Git read | No | Prompts/output may contain secrets | LEGACY | Medium |
| 13 | `apos report` | Derive quality report from run log / `cmd_report` | `cli`, `report`, `runlog`, `git` | Direct run artifacts | No | Git read | No | Reads potentially sensitive history | LEGACY | Medium |
| 14 | `apos benchmark validate` | Validate suite and TaskSpecs / `cmd_benchmark_validate` | `cli`, `benchmark`, `models`, `git` | Direct suite/TaskSpecs | No | Git read | No | No | QUARANTINED | Low |
| 15 | `apos benchmark show` | Display suite / `cmd_benchmark_show` | `cli`, `benchmark`, `git` | Direct suite/TaskSpecs | No | Git read | No | No | QUARANTINED | Low |
| 16 | `apos benchmark run` | Run many legacy coding tasks / `cmd_benchmark_run` | `cli`, `benchmark`, `kernel`, `git`, `report` | Suite, TaskSpecs, project | Source, branches, commits, logs, result JSON | Test/coder/Git subprocesses | Conditional Ollama/network | Coder command and inherited env | QUARANTINED | Critical |
| 17 | `apos benchmark compare` | Compare stored results / `cmd_benchmark_compare` | `cli`, `benchmark`, `git` | Direct result JSON | No | Git read | No | Result metadata can expose command/model | QUARANTINED | Low |
| 18 | `apos benchmark results list` | List benchmark results / `cmd_benchmark_results_list` | `cli`, `benchmark`, `git` | Direct result JSON | No | Git read | No | Result metadata | QUARANTINED | Low |
| 19 | `apos benchmark results show` | Show one benchmark result / `cmd_benchmark_results_show` | `cli`, `benchmark`, `git` | Direct result JSON | No | Git read | No | Result metadata and output evidence | QUARANTINED | Medium |
| 20 | `apos evolution` / CUI | Interactive self-evolution dispatcher / `cmd_evolution_orchestrator` | `cli`, `orchestrator`, `evolution`, `kernel` | Broad direct reads | Proposal, worktree, metadata, source | Git, tests, coder | Conditional Ollama/network | Config and inherited env | QUARANTINED | Critical |
| 21 | `apos evolution validate` | Validate pinned evolution policy / `cmd_evolution_validate` | `cli`, `evolution`, `benchmark`, `git` | Policy, versions, suite, TaskSpecs | No | Many Git reads | No | No | QUARANTINED | Medium |
| 22 | `apos evolution status` | Inspect baseline and candidates / `cmd_evolution_status` | `cli`, `evolution`, `git` | Policy, candidate state, source versions | No | Many Git reads | No | Candidate metadata | QUARANTINED | Medium |
| 23 | `apos evolution create` | Create candidate worktree / `cmd_evolution_create` | `cli`, `evolution`, `git` | Proposal, policy, Git | Artifact metadata and worktree | Git worktree/branch writes | No | No | QUARANTINED | High |
| 24 | `apos evolution run` | Develop candidate with legacy Kernel / `cmd_evolution_run` | `cli`, `evolution`, `kernel`, `git` | Broad project/candidate reads | Candidate source, logs, commits, metadata | Test/coder/Git subprocesses | Conditional Ollama/network | Config and inherited env | QUARANTINED | Critical |
| 25 | `apos evolution evaluate` | Run tests, benchmark, replay, write evidence / `cmd_evolution_evaluate` | `cli`, `evolution`, `benchmark`, `git` | Policy, source, result evidence | Evaluations and temporary worktrees | Direct tests, benchmark, Git worktrees | Commands may access network | Copies and extends host env | QUARANTINED | Critical |
| 26 | `apos evolution review` | Record review decision / `cmd_evolution_review` | `cli`, `evolution`, `git` | Policy, evaluation, candidate Git | Review/candidate JSON | Git reads | No | Reviewer identity is unproved | QUARANTINED | High |

No production CLI command currently exposes modern process cancellation. The
modern `ControlledExecutionService.cancel(request_id)` API exists and is tested,
but no production adapter owns a cancellation request. Legacy timeouts do not
establish durable cancellation ownership.

## 5. Actual Privileged Call Graphs

### 5.1 Setup and configuration

```text
apos init
  -> cmd_init
  -> GitClient.ensure_repo
  -> GitClient.run -> subprocess.run(["git", ...])
  -> ensure_project_memory
  -> Path.mkdir / Path.write_text under .apos

apos bootstrap / connect / connect-ollama
  -> GitClient.ensure_repo -> direct Git subprocess
  -> ensure_project_memory / load_config / save_config
  -> direct Path reads and writes under .apos
```

No workspace, capability, authorization, or audit service mediates these writes.
`connect` also persists a future arbitrary process-launch string. The connect
operation itself does not launch it, but it changes later execution authority.

### 5.2 Status and inspection

```text
apos status
  -> GitClient.ensure_repo/current_branch/status_porcelain
  -> GitClient.run -> subprocess.run(["git", ...])
  -> load_config -> Path.read_text

apos runs list/show
  -> GitClient.ensure_repo -> direct Git subprocess
  -> runlog list/load
  -> Path.glob / Path.read_text under .apos/runs

apos report
  -> GitClient.ensure_repo
  -> generate_quality_report
  -> load_run_log -> direct Path.read_text
```

The run-log resolver canonicalizes a requested path beneath `.apos/runs`, which
is useful path hygiene, but this is not modern authorization. The inspection
paths do not use `ProjectRuntime`, capability policy, or audit. They may expose
stored prompts, model output, test output, replacement content references, and
configured command strings.

### 5.3 Validate

```text
apos validate
  -> ProjectRuntime.create_read_only
  -> TaskSpecValidationService.validate
  -> FileSystemService.read_file
  -> ProjectWorkspace.resolve and SecretPolicy
  -> AuthorizationService.authorize(PROJECT_READ)
  -> PermissionEngine
  -> AuditLog REQUESTED/AUTHORIZED/STARTED/COMPLETED
  -> Path.read_text
  -> TaskSpec.from_mapping
  -> ToolResult
```

This is the sole privileged production path currently migrated. It deliberately
does not call `TaskSpec.load`, `Kernel`, `GitClient`, or `subprocess`.

### 5.4 Draft and refine

```text
apos draft
  -> GitClient.ensure_repo -> direct Git subprocess
  -> draft_task_spec -> next_task_id
  -> (root/tasks).glob -> Path.read_text
  -> optional write_task_spec -> mkdir / Path.write_text

apos refine
  -> GitClient.ensure_repo
  -> configured_ollama / load_config -> direct config read
  -> TaskSpec.load -> direct Path.read_text
  -> refine_task_spec_with_ollama
     -> urllib.request.urlopen(configured_host)
     -> fallback run_ollama_prompt
        -> subprocess.run([configured_binary, "run", model])
  -> optional write_task_spec -> direct Path.write_text
```

`--output` is joined with the repository root but is not passed through
`ProjectWorkspace`; traversal and absolute-path behavior is therefore outside the
modern project boundary. Refine sends the complete TaskSpec to a configurable
HTTP host before falling back to an executable.

### 5.5 Legacy run

```text
apos run
  -> TaskSpec.load -> direct Path.read_text
  -> Kernel.run_task
     -> GitClient.ensure_repo/status/checkout/exclude
        -> subprocess.run(["git", ...])
        -> direct .git/info/exclude write
     -> RunRecorder -> direct .apos/runs writes
     -> executor.run_commands(test_commands)
        -> parse_legacy_command
        -> subprocess.run(argv, shell=False, inherited environment)
     -> CommandPatchCoder.run
        -> temporary directory
        -> subprocess.run(configured argv, shell=False, inherited environment)
     -> build_coder_prompt
        -> direct source Path.read_text
     -> patch response
        -> GitClient.apply_patch -> direct Git subprocess
        OR Kernel._apply_file_replacement -> direct Path.write_text
     -> executor.run_commands(test_commands) again
     -> rollback
        -> GitClient.reverse_patch OR direct write/unlink
     -> GitClient.commit -> add/commit subprocesses
     -> RunRecorder summary writes
```

The legacy `PermissionManager` validates declared patch paths, but it is not the
modern `PermissionEngine` and cannot constrain a test or coder subprocess at the
OS level. Preflight tests run before a coder command is selected and before any
modern permission or persistent task exists. `shell=False` removes shell parsing
but does not prevent interpreter code, child processes, filesystem escape,
network access, or inherited-environment access.

### 5.6 Benchmark

Read-only benchmark commands use direct suite, TaskSpec, result, and Git reads.
The execution path is:

```text
apos benchmark run
  -> run_benchmark_suite
  -> GitClient ensure/status/branch operations
  -> for each TaskSpec: Kernel.run_task
     -> entire legacy run graph
  -> generate_quality_report -> legacy run-log reads
  -> direct result JSON write under .apos/benchmarks
  -> GitClient checkout(start_branch) in finally
```

This multiplies the legacy `run` blast radius and mutates branches repeatedly.
Benchmark is development evaluation tooling, not a user project control-plane
capability, so migration is not prioritized.

### 5.7 Evolution and CUI

```text
apos evolution create
  -> validate_evolution -> direct policy/TaskSpec/Git reads
  -> GitClient worktree add -b
  -> direct candidate metadata write

apos evolution run
  -> load candidate/policy
  -> Kernel(candidate_worktree).run_task
  -> entire legacy run graph
  -> direct candidate metadata update

apos evolution evaluate
  -> direct policy/source/evidence reads
  -> GitClient lineage/diff/worktree operations
  -> evolution._execute
     -> subprocess.run(parse_legacy_command, shell=False,
                       env=copy(os.environ)+PYTHONPATH)
  -> benchmark command -> legacy benchmark and Kernel
  -> trusted replay worktrees -> more direct test subprocesses
  -> direct evaluation JSON/Markdown writes

apos evolution review
  -> direct metadata/report reads and hashes
  -> GitClient commit lookup
  -> direct review and candidate metadata writes
```

The interactive CUI is a dispatcher over all of these paths and additionally
writes proposal JSON directly. It labels typed strings as Codex or human review
decisions without identity proof. It must not become the foundation of the new
product control plane.

### 5.8 Direct operation index

The production-reachable direct primitives are concentrated at these boundaries:

| Primitive | Production owner | Modern control plane? |
| --- | --- | --- |
| `subprocess.run` for Git | `GitClient.run` | No |
| `subprocess.run` for tests | `executor.run_command`, `evolution._execute` | No |
| `subprocess.run` for coder | `CommandPatchCoder`, Ollama CLI fallback | No |
| `subprocess.Popen` | `ControlledExecutionService.run` | Yes, but not used by a production command yet |
| `Path.read_text/read_bytes` | config, models, coder, runlog, benchmark, evolution | No, except the final read inside `FileSystemService` |
| `Path.write_text` | config, draft, Kernel, runlog, benchmark, evolution, orchestrator | No |
| `os.replace` / temporary unlink | `FileSystemService._atomic_write` | Yes |
| direct unlink | Kernel rollback | No |
| Git branch/apply/commit/worktree | `GitClient` callers | No |
| HTTP `urlopen` | Ollama adapter | No |
| environment read/copy | config, controlled execution, evolution | Mixed by subsystem; production legacy paths are not sanitized |

There is no production `os.system` or Python `shell=True` call at this baseline.
That fact does not close the listed process and interpreter attack surfaces.

## 6. Modern / Legacy / Mixed Classification

### MODERN

- `apos validate`: all project-file access crosses the reviewed runtime,
  authorization, filesystem, and audit boundaries.
- `apos task-template`: has no project, process, Git, network, environment, or
  secret operation. A runtime would add ceremony without authority value.

### LEGACY

- `apos init`
- `apos bootstrap`
- `apos connect`
- `apos connect-ollama`
- `apos status`
- `apos draft`
- `apos refine`
- `apos run`
- `apos runs list`
- `apos runs show`
- `apos report`

These commands use direct privileged paths and do not reach the modern
authorization boundary. Path normalization or `shell=False` inside a legacy
module is a useful local control, but it does not change this classification.

### MIXED

None at the audited baseline. If only test execution is migrated inside
`apos run`, that command must become MIXED, not MODERN. The classification may
change to MODERN only when all remaining filesystem, Git, Local LLM, network,
history, and process paths are mediated or removed.

### QUARANTINED

- all six benchmark leaf commands;
- the default evolution CUI mode;
- all six evolution subcommands.

Quarantine is a product disposition, not a security claim. These paths still use
legacy privileged mechanisms. They remain local development/release tools and
must not be exposed as the production control plane.

## 7. Product Value Assessment

| Surface | Product value | Reason |
| --- | --- | --- |
| `validate` | Medium | Safe project input inspection supports later task control but is not execution itself. |
| `status` | High | User-owned visibility into project, task, policy, and execution state is core control-plane value. |
| `run` test execution | High | Local execution authority, approval, cancellation, recovery, and audit are central APOS responsibilities. |
| `run` Local LLM patch generation | Low | Code-generation intelligence belongs to interchangeable AI providers. |
| `runs list/show` and `report` | High | Durable execution evidence belongs to the user and must outlive any AI provider. |
| `init/bootstrap` | Medium | Project registration and policy initialization support the control plane. |
| `connect` / `connect-ollama` | Low | Provider configuration is optional adapter plumbing, not the product center. |
| `draft` / `refine` | Low | Planning and prose refinement are better owned by high-level AI systems. |
| benchmark | Low | Useful for APOS development and release evaluation, not the runtime product. |
| self-evolution and CUI | Low | Release engineering for APOS itself is not a user project control-plane capability. |
| future Git read/write service | High | User-owned local change state and explicit mutation authority are core, but must be separated by capability. |

The distinction is not whether a feature is useful. It is whether APOS must own
its authority and durable state. APOS should own execution, approval, task,
recovery, audit, and project/Git boundaries. It should not compete to own general
planning or coding intelligence.

## 8. Legacy Subsystem Disposition

| Subsystem | Disposition | Rationale |
| --- | --- | --- |
| `Kernel` monolith | RETIRE LATER | Extract only valuable control-plane slices; do not transplant its coder/Git/file/test coupling. |
| `CommandPatchCoder` | QUARANTINE | Keep as an optional local provider prototype until a provider-neutral adapter exists; never make it an authority boundary. |
| `executor.run_command(s)` | RETIRE LATER | Replace every production call with controlled execution; do not add new callers. |
| `GitClient` read operations | MIGRATE | Move required reads behind an authorized, audited `GIT_READ` service. |
| `GitClient` write workflow | MIGRATE | Branch/apply/commit need separate `GIT_WRITE` approval and later containment; no adapter may call them directly. |
| Ollama integration | QUARANTINE | Retain only as an optional proposal/provider adapter; network and binary fallback need explicit capabilities before reuse. |
| self-evolution | QUARANTINE | Keep as local release-development tooling outside production runtime. |
| evolution CUI | RETIRE LATER | It centers the old product identity and records unproved review identities. |
| benchmark execution | QUARANTINE | Keep for development evaluation; do not spend convergence budget migrating it now. |
| legacy run-log format | KEEP temporarily | It is useful evidence for compatibility, but new durable history should be generated by the modern task/audit plane. |

`GitClient` is not retired wholesale because argv-based Git invocation is a
useful low-level mechanism. Its current public direct-call shape is the problem.
It should become infrastructure behind capability services, never an adapter API.

## 9. Migration Candidate Ranking

| Rank | Candidate | User value | Security value | Architecture value | Complexity | Legacy dependency | Current bypass risk | Capabilities | Approval | Sandbox dependency |
| ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `apos run`: all TaskSpec test executions | High | High | High | High but bounded | `Kernel`, `executor`, string commands | Critical | `PROJECT_READ`, `TEST_EXECUTE`; conditional `NETWORK_ACCESS`; explicit output-write policy | Yes for exact execution request | No for routing; yes before untrusted remote use |
| 2 | `apos status` | High | Medium | Medium | Low to medium | `GitClient`, config reads | Medium | `PROJECT_READ`, `GIT_READ`, internal task/audit read | No for local low-risk reads | No |
| 3 | `apos runs show` plus `report` inspection family | High | Medium | High | Medium | legacy run-log storage and direct `.apos` reads | Medium | internal history read; possibly a future `TASK_READ`/`AUDIT_READ` capability | Usually no; secret reveal may require policy | No |
| 4 | `apos init` / `bootstrap` project registration | Medium | Medium | Medium | Medium | direct config/memory writes and Git read | Medium | `PROJECT_WRITE`, `GIT_READ`; provider config separately | Explicit confirmation for writes | No |

Candidate 1 is selected because it is the smallest existing production slice
that exercises the execution-control identity of APOS. Candidate 2 is easier and
should follow soon, but it would prove only another read-only adapter. Candidate
3 is strategically important because user-owned history is core product state,
but the current logs are generated by the legacy Kernel and contain a different
data model from persistent tasks and audit events.

Full `apos run` migration is not the recommendation. It would combine test
execution, Local LLM invocation, file mutation, Git mutation, rollback, logging,
approval UX, and cancellation in one review unit. That would be too broad and
would obscure which authority boundary actually improved.

## 10. Controlled Test Execution Assessment

### Decision

Controlled test execution is appropriate as the second migration **only** as a
strictly local, narrow slice. It is not safe evidence of an OS sandbox and must
not enable remote AI exposure.

### Required capabilities in real tests

| Activity | Capability intent | Current physical behavior |
| --- | --- | --- |
| Read source, tests, configuration, fixtures | `PROJECT_READ` | Interpreter can read any host-user-readable path. |
| Launch the trusted interpreter | `TEST_EXECUTE` | `ControlledExecutionService` authorizes and audits the initial executable. |
| Spawn test workers or helper tools | `PROCESS_EXECUTE` or bounded child authority | Children inherit host-user authority; process-count limit is not enforced. |
| Write `__pycache__`, `.pytest_cache`, coverage, snapshots | `PROJECT_WRITE` or approved scratch output | Child writes are not intercepted by `FileSystemService`. |
| Use OS temp files | Runtime scratch authority | Sanitizer points `TEMP`/`TMP` to `.apos/tmp`, but Python libraries may use other OS APIs. |
| Reach package indexes, services, browsers, databases | `NETWORK_ACCESS` | Current network policy is `DECLARATIVE_ONLY`, not isolation. |
| Read environment | Explicit environment allowlist | Sanitizer reduces inheritance, but this is not an OS secret boundary. |

`TEST_EXECUTE` labels the intent of the initial process. It does not imply that
the process has only read access or only test behavior. A trusted Python command
can import `subprocess`, `socket`, `ctypes`, or filesystem APIs. It can re-enter a
shell explicitly, modify Git, inspect the host, and spawn descendants. The
current service reports `memory_limit_enforced=False`,
`process_count_limit_enforced=False`, and `network_enforcement=DECLARATIVE_ONLY`.

### Fit with the existing modern core

The intended control path is feasible without implementing a general
ExecutionBroker:

```text
cmd_run adapter
  -> ProjectRuntime with explicit local-test profile
  -> TaskSpecValidationService (authorized TaskSpec read)
  -> small TestExecutionService
  -> canonical command preparation
  -> TaskService persistent task and approval lifecycle
  -> TaskService.run_command_task
  -> ControlledExecutionService
  -> AuthorizationService / PermissionEngine
  -> AuditLog
  -> ToolResult / test result compatibility adapter
```

The current integration test constructs the exact execution
`PermissionRequest` metadata separately from `ControlledExecutionService`. That
duplication is acceptable as test setup but unsafe as a production adapter
pattern. One core-owned canonical preparation function must produce the exact
resolved executable, argv digest, cwd, environment digest, network policy,
limits, operation, risk, request ID, and task ID used both for persistence and
execution. The CLI must not reproduce that metadata.

### Approval and persistent ownership

- Each command must have an immutable request ID and a durable task ID.
- A command must not start until its exact persisted request approval is consumed
  atomically and the task enters `RUNNING`.
- Current unauthenticated local user approval may be used only for a local CLI
  checkpoint with an explicit warning. It is not identity proof.
- Network approval must be distinct and exact. Because enforcement is currently
  declarative, approval records intent but does not create isolation.
- A multi-command test plan should use one execution task per exact command, all
  correlated by one run ID. This matches the current one-request/one-approval
  task model and avoids inventing a broad batch grant.

### Cache and mutation policy

Tests commonly write even when they are conceptually verification-only. The
migration must choose and document one policy rather than silently allowing
writes:

1. Prefer disabling bytecode/cache writes and redirecting supported caches and
   temporary output to APOS internal scratch space.
2. Declare known project output paths when a suite genuinely needs snapshots,
   coverage, or generated fixtures.
3. Record observed changed paths after execution as evidence.
4. Do not claim those steps prevent arbitrary interpreter writes. OS containment
   is required for enforcement.

### Cancellation

The test service, not the CLI adapter, owns the active request ID. On timeout or
explicit cancellation it calls `ControlledExecutionService.cancel`, waits for
best-effort process-tree termination, and only then moves the persistent task to
`CANCELLED`. A Ctrl+C adapter should request cancellation and render the resulting
task state. It must not directly kill a process and bypass task/audit closure.

### Migration precondition conclusion

The slice can be implemented before an OS sandbox because routing, durable
ownership, and audit are valuable independently. Preconditions are:

- local trusted-user invocation only;
- current-Python-only executable policy initially;
- network policy defaults to denied, with an explicit statement that denial is
  not physically enforced;
- no remote/MCP/API exposure;
- no compatibility fallback to `executor.run_commands` after modern preparation
  or authorization fails;
- no claim that project or network escape is prevented.

## 11. Recommended Next Vertical Slice

1. **Selected command:** `apos run`, limited to every execution of TaskSpec
   `test_commands` in preflight and post-change verification.
2. **Why now:** It closes the highest-value process bypass that can be isolated
   from coder, file, and Git migration while exercising the existing P0-3A task
   control plane.
3. **Product identity:** Execution authority, approval, cancellation, recovery,
   and evidence remain user-owned and independent of the coding AI.
4. **Capabilities:** `PROJECT_READ` for TaskSpec input, `TEST_EXECUTE` for each
   command, optional explicit scratch/output policy, and separately modeled
   `NETWORK_ACCESS` when requested.
5. **Authorization model:** An explicit runtime profile; fail-closed rules;
   canonical exact-request construction; no policy in the CLI adapter.
6. **Approval:** Required for each exact test execution at this stage. Batch or
   wildcard approval is out of scope.
7. **Persistent task:** Required, one task per command with run-level correlation.
8. **Process execution:** Required through `TaskService.run_command_task` and the
   runtime-bound `ControlledExecutionService` only.
9. **Without sandbox:** Migration is possible for local trusted use, but it does
   not make the command safe for an untrusted AI or hostile test code.
10. **Bypass removal:** Replace all production calls from `Kernel` to
    `executor.run_commands`; prohibit fallback; add spies and source checks that
    fail if the migrated test path reaches legacy executor or direct subprocess.
11. **Minimum boundary:** TaskSpec authorized read, canonical test request,
    persistent approval/task lifecycle, controlled process, cancellation,
    structured result, and correlated audit. No coder or Git redesign.
12. **Do not do:** Do not migrate Local LLM generation, patching, file
    replacement, rollback, Git, run-log redesign, benchmark, evolution,
    ExecutionBroker, sandbox, MCP, or remote control in this checkpoint.
13. **Expected risk:** Interpreter escape, declarative-only network control,
    child-process escape, unproved human identity, mutable audit storage,
    TOCTOU, and incomplete cancellation under host/OS failure.
14. **Regression tests:** Cover all preflight/post-change call sites, exact
    approval binding, persistent transitions, fail-closed fallback, output bounds,
    environment reduction, cancellation, timeout, process-tree behavior,
    network intent, and unchanged CLI result semantics.

The checkpoint name should make the partial state explicit, for example
`P0-3A.7 Controlled Test Execution Migration`. It must not state that
`apos run` is fully modern.

## 12. Proposed Minimal Architecture

### Adapter

Keep `cmd_run` thin. It may translate `ToolResult` and persistent task states into
the established CLI exit code and output, but it must not parse authority rules,
construct approval digests, launch processes, or mutate task state directly.

### Runtime profile

Add one explicit local-test profile beside `create_read_only`. Its initial rules
should be narrowly scoped:

- `PROJECT_READ`: `ALLOW` for the selected TaskSpec and needed project metadata;
- `TEST_EXECUTE`: `APPROVAL_REQUIRED`;
- `NETWORK_ACCESS`: `DENY` by default;
- every unspecified capability: `DENY`;
- executable policy: current Python only for the first slice.

The profile name must describe local control, not sandboxing. Runtime construction
must remain composition-only and must not call recovery.

### Service boundary

Introduce one small runtime-owned test orchestration service, or extend an
equivalent existing core boundary, with only these responsibilities:

1. accept a validated TaskSpec and actor;
2. convert each supported test command to canonical executable plus argv;
3. ask one core-owned preparation path for the exact permission request;
4. coordinate task creation, queueing, approval, execution, and result mapping;
5. stop on the first failed command to preserve current semantics;
6. correlate every command to the parent APOS run.

It is not a general dispatcher, workflow engine, Git service, coder service, or
ExecutionBroker.

### Authorization boundary

Command assessment and permission-request construction must have one source of
truth. Assessment results must be immutable or revalidated immediately before
launch. The persisted approval digest must cover resolved executable identity,
argv, cwd, environment, network policy, timeout, output limits, task ID, and
project ID.

### Execution boundary

Only `ControlledExecutionService` launches the process. `shell=False` is retained.
No production fallback may call `executor.run_command`, `subprocess.run`,
`subprocess.Popen`, `os.system`, PowerShell, or `cmd.exe` directly.

### Task ownership

Use one derived task ID per test command and one parent correlation ID per APOS
run. The actor that owns the task must match the execution request subject. Task
state changes occur only through `TaskService`.

### Audit lifecycle

At minimum, persist and correlate:

```text
TASK_CREATED
TASK_QUEUED
TASK_WAITING_APPROVAL / APPROVAL_REQUESTED
APPROVAL_GRANTED / TASK_APPROVED
execution REQUESTED / AUTHORIZED
APPROVAL_CONSUMED / TASK_RUNNING
execution STARTED
execution COMPLETED | FAILED | CANCELLED
TASK_SUCCEEDED | TASK_FAILED | TASK_CANCELLED
```

Audit metadata must use argument and environment digests rather than storing
secrets or unbounded command output. Returned output remains bounded and redacted.

### Cancellation ownership

The test orchestration service owns request IDs and invokes the bound execution
service cancellation API. State becomes `CANCELLED` only after execution stop is
confirmed. On a later dedicated owner startup, explicit recovery may move stale
`RUNNING` tasks to `RECOVERY_REQUIRED`; ordinary CLI inspection or runtime
construction must never do so.

## 13. Required Invariants

1. Runtime construction performs dependency composition only.
2. Recovery occurs only through `recover_interrupted_tasks()` called by a
   dedicated execution owner.
3. Every migrated test process passes through `TaskService` and the runtime-bound
   `ControlledExecutionService`.
4. No migrated test path calls legacy executor or direct process APIs.
5. Failure in parsing, preparation, policy, authorization, approval, persistence,
   audit, or launch fails closed with no legacy fallback.
6. The CLI adapter cannot select its own permission policy or executor.
7. The exact executable, argv, cwd, environment, network intent, limits, project,
   actor, request, and task are approval-bound.
8. A persistent approval is single-use and consumed before process start.
9. The task enters `RUNNING` atomically with approval consumption.
10. `shell=False` remains mandatory, but documentation never equates it with a
    sandbox.
11. The initial executable allowlist contains only the current Python executable.
12. Project-relative executable resolution and project PATH injection fail closed.
13. Output is bounded and secrets are redacted from returned results and audit.
14. Network defaults to denied; the result states enforcement is declarative only.
15. Cancellation owns the process request and task transition together.
16. Test cache/temp behavior is explicit and recorded.
17. Existing CLI pass/fail ordering and stop-on-first-failure behavior remain
    compatible.
18. `apos run` is classified MIXED after this slice until every other privileged
    path is migrated or retired.
19. No remote AI, MCP, API, Discord, or GUI adapter may reach this local-only path.
20. Runtime, source, test, and documentation must not claim OS-level containment.

## 14. Required Regression Tests

### Existing evidence to preserve

- `tests/test_cli_control_plane.py::test_production_cli_reads_through_runtime_and_records_audit_lifecycle`
- `tests/test_cli_control_plane.py::test_cli_adapter_does_not_reenter_legacy_validate_dependencies`
- `tests/test_cli_control_plane.py::test_validate_does_not_recover_an_unrelated_running_task`
- `tests/test_core_runtime.py::test_runtime_requires_explicit_policies`
- `tests/test_core_runtime.py::test_read_only_profile_allows_project_read_and_denies_write`
- `tests/test_core_execution.py::test_runs_without_shell_inside_project_and_audits_lifecycle`
- `tests/test_core_execution.py::test_rejects_shell_syntax_and_project_path_hijacking`
- `tests/test_core_execution.py::test_rejects_outside_and_junction_working_directories`
- `tests/test_core_execution.py::test_timeout_kills_child_process_tree`
- `tests/test_core_execution.py::test_external_cancellation_kills_running_process`
- `tests/test_core_execution.py::test_bounds_large_output`
- `tests/test_core_execution.py::test_sanitizes_environment_and_redacts_secret_output_and_audit`
- `tests/test_core_execution.py::test_rejects_environment_injection_and_explicit_network_command`
- `tests/test_core_execution.py::test_network_access_has_separate_approval_request`
- `tests/test_core_tasks.py::test_persistent_task_requires_approval_even_when_capability_policy_allows`
- `tests/test_core_tasks.py::test_two_workers_cannot_consume_one_approval_or_start_one_task_twice`
- `tests/test_core_tasks.py::test_explicit_runtime_recovery_marks_running_task_without_automatic_execution`
- `tests/test_core_tasks.py::test_runtime_execution_consumes_persistent_approval_before_process_start`

### New migration tests required

1. A real `main(["run", ...])` path sends preflight tests through the runtime test
   service and never through `executor.run_commands` or direct subprocess.
2. Post-change verification uses the same modern path for patch and file-
   replacement branches.
3. Every supported test command is canonicalized to the approved executable and
   argv; unsupported executables fail closed.
4. A changed argument, cwd, environment, limit, network policy, task ID, or actor
   invalidates approval.
5. A command cannot start from `CREATED`, `QUEUED`, or `WAITING_APPROVAL`.
6. Successful, failed, timed-out, and cancelled commands close both task and
   execution audit lifecycles.
7. Ctrl+C requests controlled cancellation and does not directly kill or abandon
   the persistent task.
8. Runtime construction during `run` does not recover unrelated tasks.
9. No network approval silently converts declarative policy into an isolation
   claim.
10. Cache/temp settings are deterministic and no unexpected project changes are
    omitted from result evidence.
11. Existing `RunSummary`, exit codes, command order, and stop-on-first-failure
    semantics remain compatible.
12. A source-level guard finds no production import or call from the migrated
    test path to `apos.executor`.
13. Spies fail if the adapter constructs its own `PermissionRequest` metadata or
    directly calls `ControlledExecutionService.run` instead of `TaskService`.
14. Corrupted task storage, audit failure, or preparation mismatch prevents
    process start and returns a stable error without secret disclosure.

## 15. Explicit Non-Goals

This planning step and the recommended next implementation do not include:

- implementation in this research branch;
- full `apos run` migration;
- Local LLM or Ollama migration;
- patch generation, file replacement, rollback, or Git migration;
- a general ExecutionBroker or mandatory repository-wide dispatcher;
- an OS sandbox, Windows Sandbox, container, Job Object, restricted token, or
  filesystem virtualization;
- physical network isolation;
- authenticated human identity;
- tamper-resistant remote audit storage;
- benchmark or self-evolution migration;
- MCP, Discord, GUI, API, or remote control;
- a second production command migration in this document.

## 16. Residual Risks

The recommended slice leaves significant risks:

- Python is a general interpreter and can escape the APOS logical project
  boundary with the host user's OS rights.
- Child processes can use other executables, shells, native libraries, OS APIs,
  and network sockets even though the initial launch used `shell=False`.
- `NETWORK_DENIED` is policy intent only; there is no firewall, token, namespace,
  or container enforcement.
- Memory and process-count limits are represented but not enforced.
- Windows process-tree termination through `taskkill` is best effort and is not a
  durable Job Object boundary.
- Environment reduction does not prevent discovery of secrets through files,
  credential stores, inherited user identity, local services, or OS APIs.
- Approval is local user intent without authenticated human identity proof.
- Audit files and the SQLite task store are mutable by the APOS user and by an
  escaped subprocess.
- Workspace resolution and later process access have TOCTOU and reparse-point
  risks without handle-based OS enforcement.
- The rest of `apos run` remains a direct privileged bypass after test execution
  migration, so the command remains MIXED.
- Legacy benchmark, evolution, Git, coder, and Ollama modules remain importable
  and must stay unreachable from future remote adapters.

Therefore APOS still cannot be safely exposed to an untrusted external AI over a
network, cannot provide a true OS-level project sandbox, and cannot prevent a
trusted interpreter from escaping the logical project boundary.

## 17. Recommended Implementation Sequence

1. Freeze a review branch from the approved master and characterize all current
   `apos run` test invocation points and compatibility outputs.
2. Define one explicit local-test runtime profile with current-Python-only command
   policy and fail-closed capability rules.
3. Add one canonical core-owned command preparation path so persistence and
   execution cannot disagree on approval metadata.
4. Add the smallest runtime-owned test orchestration service using one persistent
   task per exact command and one run correlation ID.
5. Migrate the preflight `run_commands` call and prove no legacy fallback.
6. Migrate both post-patch and post-file-replacement verification calls in the
   same checkpoint so no TaskSpec test path remains direct.
7. Wire cancellation and timeout closure through the same service and task owner.
8. Preserve CLI output and RunSummary compatibility through a result adapter;
   keep legacy coder, file, Git, and run-log paths visibly isolated.
9. Add behavioral spies, lifecycle tests, source guards, and all existing
   regression tests; perform an independent architecture/security review.
10. Label `apos run` MIXED and stop. Do not begin Git, coder, sandbox, or remote
    work in the same checkpoint.
11. Migrate `apos status` next to provide modern user-owned inspection of project,
    task, policy, and audit state.
12. Design modern execution-history inspection before migrating `runs show` and
    `report`, avoiding a permanent dependency on the legacy Kernel log format.
13. Only after production bypass closure should APOS proceed to OS containment,
    authenticated identity, audit tamper resistance, and then remote adapters.

The next implementation review must judge the slice by one question: did every
production TaskSpec test process become a persistently owned, authorized,
audited, cancellable APOS operation without implying that the interpreter is
sandboxed? If the answer is not unambiguously yes, the slice is incomplete.
