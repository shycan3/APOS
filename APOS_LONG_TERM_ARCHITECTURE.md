# APOS Long-Term Architecture

Status: Directional architecture after P0-3A

Baseline: `dd55f31e1acfd1101adb732cacd3290ad021b273`

## Product identity

APOS is a project-scoped local execution runtime for a human and a higher-level AI. It turns authenticated intent into durable, authorized, isolated, observable work against one local project.

APOS is not intended to be:

- a general autonomous agent;
- an unrestricted self-modifying system;
- an internet-exposed remote shell;
- a replacement for a high-level reasoning model;
- a promise that policy checks alone create an OS sandbox.

The product promise is narrower and more useful: a high-level AI can request local project operations that it cannot perform itself, while APOS preserves task state, asks the right human for approval, constrains execution, returns evidence, and leaves a recoverable project state.

## Current architecture from the code

### Modern core

`src/apos/core` is transport-neutral and is the intended foundation:

| Module | Current responsibility | Long-term responsibility |
|---|---|---|
| `runtime.py` | Composes workspace, audit, tasks, policy, authorization, filesystem, execution, and tool metadata | Composition root for one project runtime; no transport logic |
| `workspace.py` | Canonical project-relative resolution and secret policy | Logical project identity and path policy; never claim OS isolation |
| `filesystem.py` | Authorized file operations returning `ToolResult` | Sole project filesystem capability facade |
| `permissions.py` | Actors, capabilities, risk, decisions, grants, and authorization | Deterministic policy plus authenticated authority evidence |
| `audit.py` | Redacted append-oriented event log | Durable, tamper-evident, cross-process audit sink |
| `execution.py` | Command assessment and controlled host subprocess | Front end to the execution broker; provider-independent request/result types |
| `tasks.py` | SQLite tasks/approvals, lifecycle, claim, recovery, and service API | Official durable control plane and authority boundary |
| `tools.py` | Tool metadata registry | Capability catalog only, or later paired with a separate mandatory dispatcher |
| `commandline.py` | Legacy command parsing without a shell | Compatibility parser to be retired from privileged paths |
| `result.py` | Transport-neutral result/error envelope | Stable adapter-facing result contract |

P0-1, P0-2, and P0-3A form a dependency chain, not three independent feature groups. Path policy feeds authorization resources; authorization and audit surround operations; tasks bind durable lifecycle and approvals to execution.

### Legacy and prototype plane

The current user-facing CLI/CUI constructs the legacy `Kernel`, `GitClient`, coder, executor, and evolution/orchestration objects. These paths directly perform some filesystem, Git, HTTP, and subprocess operations. They remain useful prototypes and compatibility references, but they are not the long-term privileged control plane.

Specific drift risks are:

- two permission models (`core.permissions` and legacy `permissions`);
- two path-control models (`ProjectWorkspace` and legacy path helpers/direct paths);
- direct subprocess calls in executor, coder, Ollama, Git, and evolution-related paths;
- a CLI that does not compose `ProjectRuntime`;
- `ToolRegistry` metadata that a future adapter could mistake for callable authorization;
- task identity and approval persistence that are not yet the universal entry path;
- local-model protocol and execution behavior coupled through the legacy kernel.

No remote adapter should be added until these privileged paths converge behind the modern runtime.

### Test and documentation evidence

The current separation is visible in the test suite as well as the imports:

- `tests/test_core_runtime.py::test_composes_one_project_scoped_runtime_and_tool_registry` confirms the core composition root, while `test_runtime_requires_explicit_policies` confirms fail-closed construction.
- `tests/test_core_filesystem.py` covers project-relative reads/writes, atomic write behavior, secret paths, traversal, symlink escape, and aliases to internal state.
- `tests/test_core_security.py` covers exact-request approval binding, AI/system rejection, fail-closed authenticated-human semantics, lifecycle audit, and redaction.
- `tests/test_core_execution.py` covers `shell=False` argument handling, shell-syntax rejection, outside/junction working directories, timeout/cancellation, process-tree termination, output bounds, environment sanitation, and declarative network approval.
- `tests/test_core_tasks.py` covers persistence, state transitions, one-time approval consumption, concurrent claims, restart recovery, ownership convention, audit linkage, and runtime consumption before process start.
- `tests/test_kernel.py`, `tests/test_orchestrator.py`, `tests/test_evolution.py`, and `tests/test_ollama.py` exercise the parallel prototype plane. Their existence is useful compatibility coverage but does not prove those flows pass through the core.
- `ARCHITECTURE_AUDIT.md`, `SECURITY_THREAT_MODEL.md`, `P0_2_IMPLEMENTATION.md`, and `P0_3A_IMPLEMENTATION.md` already document the logical/OS boundary distinction and P0-3A control-plane intent.

The architectural migration should preserve behavior tests where behavior remains supported, while adding end-to-end tests that prove the adapter-to-core call path. Merely keeping both test groups green would not prove convergence.

## Target component architecture

```text
Human interfaces                 AI interfaces
Desktop approval UI       ChatGPT / high-level AI       Local model
         |                         |                         |
         +-------------------------+-------------------------+
                                   |
                         Authenticated adapters
                    (local UI, relay, MCP, GitHub)
                                   |
                         Protocol normalization
                                   |
                      Task and Authority Gateway
                                   |
                              TaskService
                 +-----------------+------------------+
                 |                                    |
        AuthorizationService                      Audit sink
                 |                                    |
       Capability Dispatcher <-> Tool catalog         |
                 |
        +--------+---------+----------------+
        |                  |                |
 FileSystemService   ExecutionBroker   GitService
                           |
                 IsolationProvider(s)
                           |
              staged workspace + artifacts
```

### Mandatory call path

For privileged operations the invariant is:

```text
authenticated caller
  -> task authority check
  -> task transition/claim
  -> permission and approval evaluation
  -> capability dispatcher
  -> service/broker
  -> operation
  -> audit and durable task result
```

An adapter may translate, authenticate, rate-limit, and present. It may not authorize, approve on behalf of a human, dispatch directly, or mutate task state through the repository.

## Responsibility boundaries

### High-level AI

The high-level AI owns semantic reasoning:

- understand user goals and repository evidence;
- decompose work and propose task manifests;
- select a declared capability and requested execution profile;
- write or review candidate patches when it has sufficient context;
- interpret test results and decide the next proposal;
- explain risk and request human approval.

It does not possess execution authority merely because it created a task. Its output is untrusted structured input until validated by APOS.

### APOS runtime

APOS owns local truth and enforcement:

- bind a request to a project, caller, task, attempt, policy version, and source revision;
- validate schemas and canonical resources;
- persist state and enforce legal transitions;
- obtain and consume authenticated approvals;
- dispatch only through authorized services;
- stage and isolate execution;
- capture bounded output, diffs, artifacts, and audit evidence;
- reconcile interrupted work;
- present results without treating output as instructions.

APOS should not silently rewrite the high-level AI's intent or become a second unconstrained planner.

### Local LLM

A local model is an optional compute resource behind a narrow model adapter. It is not an actor with implicit project authority and not part of the trusted computing base.

Recommended uses:

- summarize local-only code or logs under a bounded context contract;
- propose a small implementation for a pre-scoped task;
- classify failures or generate candidate tests;
- redact or transform content only when deterministic verification follows.

Its output must be parsed as a proposal, never executed directly. Tool calls emitted by a local model enter the same task, authorization, and execution path as high-level AI calls.

### Adapters

Adapters are deliberately thin:

- authenticate a caller or verify a transport signature;
- map external requests to versioned APOS protocol messages;
- submit/query/cancel within granted authority;
- render approvals, progress, and results;
- preserve correlation, idempotency, and replay metadata.

Adapters do not import repositories, subprocesses, or provider implementations. A Discord button, MCP tool call, local GUI action, and GitHub webhook must produce the same internal command envelope.

## Local model architecture comparison

| Model | Quality and debugging | Latency/context/hardware | Cost and complexity | APOS fit |
|---|---|---|---|---|
| A: high-level AI plans, local LLM codes | Split can keep source local, but planning/code context diverges and weak local generation can dominate quality | Model load and large coding context require substantial VRAM; handoff adds latency | Lower API token use, high orchestration and retry complexity | Not a reliable default |
| B: high-level AI plans and codes, APOS executes | Best semantic continuity and debugging; local results close the loop | Remote-model latency and cost; selected project context may leave the machine | Simplest authority model and strongest near-term quality | Recommended default |
| C: high-level AI plans, local LLM handles narrow implementation | Local privacy and useful parallel capacity with bounded blast radius | Works with smaller context if tasks are tightly scoped; hardware still variable | Moderate adapter/evaluation complexity | Recommended optional accelerator |
| D: specialized local model swarm | Potential specialization, but error attribution and consensus are difficult | Multiple model loads, scheduling, memory pressure, and long contexts | Highest operational and test burden | Research only, outside MVP |

Ollama exposes a localhost API without local authentication by default, and large coding contexts materially increase memory needs. APOS should keep it loopback-only, treat the endpoint as an untrusted local dependency, pin model identity/configuration, bound prompt and output size, and never expose it through the remote gateway. See the official [API introduction](https://docs.ollama.com/api/introduction), [authentication behavior](https://docs.ollama.com/api/authentication), and [context length guidance](https://docs.ollama.com/context-length).

## Data and control flow

### Read or inspect

1. Adapter authenticates the caller and submits a versioned request with an idempotency key.
2. APOS resolves the project and creates or references a durable task.
3. Authority and permission checks bind the caller to the requested resource.
4. `FileSystemService` performs the read; secret policy and size limits apply.
5. APOS redacts, audits, and returns a typed result.

### Execute tests

1. The high-level AI requests a named tool/capability, arguments, source revision, and risk profile.
2. APOS creates a manifest and determines the minimum effective isolation profile.
3. Required human approval binds task, attempt, manifest hash, expiry, and scope.
4. The broker stages the source and invokes a provider.
5. Provider output and artifacts are bounded and verified.
6. Task state and audit are committed before a result is released.

### Modify code

1. A candidate patch is produced by the high-level AI or optional local model.
2. APOS validates the patch against the staged base and policy.
3. Tests run against an isolated staged tree.
4. APOS returns diff and evidence for review.
5. A distinct authorized apply operation changes the project working tree.
6. A Git checkpoint is optional policy after validation; remote push is always separate.

Execution and application must remain separate. A process that can directly mutate the source project defeats review, audit clarity, and recovery.

## Project and state model

Each runtime instance is bound to one canonical project ID and one state store. Long term, durable records need:

- project ID and canonical root fingerprint;
- source control revision and dirty-tree digest;
- task, attempt, authority, approval, and policy versions;
- execution and artifact manifests;
- adapter request IDs and idempotency keys;
- append-only audit sequence and integrity metadata.

The SQLite task store may remain appropriate for a single-machine runtime. Cross-process writers require an explicit ownership/locking model. Audit integrity needs stronger storage and hash chaining before hostile workers or remote adapters are admitted.

## Git architecture

Git is a project-state service, not an execution sandbox and not the task database.

Recommended boundaries:

- inspection (`status`, `diff`, revision identity) is read capability;
- branch/worktree creation is project mutation;
- local checkpoint commit is a separate, approvable capability;
- remote fetch/push combines network, credential, and repository mutation risk;
- destructive reset/clean/force operations are excluded from the MVP;
- rollback should initially mean creating a new revert proposal or restoring an APOS-created artifact in a clean isolated worktree, never an implicit hard reset.

Linked Git worktrees are valuable for concurrent or disposable candidate work, but share repository administration and refs. They reduce workflow interference; they are not a hostile-code boundary. See [git-worktree](https://git-scm.com/docs/git-worktree.html).

## Future adapters

Preferred order:

1. Local CLI/API client migrated onto `ProjectRuntime` for end-to-end invariants.
2. Local desktop approval/recovery UI using loopback or OS-local IPC.
3. Outbound authenticated relay client for remote status and task submission.
4. ChatGPT/MCP adapter behind that gateway once identity and approval are real.
5. GitHub App adapter for repository-native asynchronous workflows.
6. Discord notification/interaction adapter only if user demand justifies it.
7. Native mobile application only after protocol stability and multi-user requirements exist.

## User value and machine placement

### Work that genuinely needs the local machine

- access to unpushed, untracked, large, private, or generated project state;
- hardware-, OS-, driver-, database-, service-, or device-specific tests;
- licensed or locally configured toolchains;
- long-running work that must preserve the user's local environment;
- local model inference or private data processing;
- controlled application of a reviewed patch to the real working tree.

### Work GitHub and cloud AI can already handle

- reasoning over committed repository content;
- review, issue triage, PR generation, and cloud-compatible CI;
- tests reproducible in hosted CI without local secrets or hardware;
- asynchronous collaboration where GitHub is the source of truth.

APOS's unique value is not duplicating cloud CI. It is making local-only capabilities remotely requestable under durable authority, isolation, evidence, and recovery.

## MVP definition

The first valuable APOS product is reached when a user can, from a trusted high-level AI interface:

1. select one registered local project;
2. inspect non-secret project state;
3. submit and monitor a durable task;
4. approve a bound execution from an authenticated human surface;
5. run a predefined test/build capability in E1 or E3;
6. receive bounded logs, status, and a verifiable diff/artifact;
7. cancel or recover interrupted work;
8. optionally apply a reviewed patch and create a local checkpoint;
9. do all of the above through the same dispatcher with a complete audit trail.

Not required for the MVP:

- autonomous self-evolution;
- arbitrary command execution;
- unrestricted package installation or internet access;
- multi-model orchestration;
- Discord, a native mobile app, or a general GUI;
- automatic remote push;
- Windows VM isolation for every task;
- destructive rollback.

## Architectural invariants

- One project runtime never operates on another project's resources.
- Every privileged operation is a task-bound capability dispatch.
- Transport identity, task authority, permission decision, human approval, and execution authority are distinct evidence.
- No model output is executable merely because it parses.
- Adapters and providers cannot bypass `TaskService` and authorization.
- Requested controls and effective OS controls are recorded separately.
- Source mutation, execution, Git checkpoint, and remote synchronization are separate operations.
- Results and repository content are untrusted inputs to every AI.
- Failure is durable: ambiguity becomes recovery state, never silent success or automatic re-execution.

## Directional conclusion

APOS should become a small, strict local runtime with replaceable interfaces around it, not a collection of feature-specific agents. The near-term architecture should optimize for one high-quality high-level AI, one durable local control plane, one trustworthy execution broker, and one simple remote path. Local models and additional interfaces add capacity only after they obey those boundaries.
