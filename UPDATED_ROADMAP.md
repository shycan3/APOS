# APOS Updated Roadmap

Status: Dependency-driven roadmap after P0-3A

Baseline: `dd55f31e1acfd1101adb732cacd3290ad021b273`

## Roadmap principle

APOS should advance by closing authority and execution paths before adding transports. A visible adapter built early would create pressure to preserve bypasses and would expose current host-level execution to remote input. The correct order is:

```text
one control plane
  -> one dispatcher
  -> enforceable execution profiles
  -> real human/device identity
  -> stable remote protocol
  -> outbound connectivity
  -> AI and UI adapters
  -> broader automation
```

Milestone names below describe dependencies rather than preserving old P0-3B/C/D labels at any cost.

## Completed foundation

### P0-1: logical project boundary

- Purpose: make a project root and protected paths explicit.
- Delivered: `ProjectWorkspace`, project-relative resolution, secret policy, controlled filesystem service.
- Guarantee: APOS core APIs can reject logical path escape and protected resources.
- Non-guarantee: an executed host process remains able to use OS APIs and the user's permissions.

### P0-2: authorization, audit, and controlled host execution

- Purpose: require explicit capability policy and record privileged operations.
- Delivered: permission decisions, authorization service, approval requirement representation, audit, command policy, `shell=False`, environment sanitation, timeout/cancel/output limits.
- Guarantee: core service calls receive deterministic policy decisions and bounded subprocess handling.
- Non-guarantee: no OS sandbox, no enforced network deny, no authenticated human identity, and incomplete defense against malicious subprocesses modifying audit/state.

### P0-3A: durable task and approval control plane

- Purpose: preserve task truth across process restarts and separate lifecycle from ad hoc calls.
- Delivered: SQLite task/approval storage, explicit state machine, one-time bound approvals, atomic claim, restart recovery, lifecycle audit, `TaskService` control plane.
- Guarantee: core task lifecycle and approval consumption are durable and constrained.
- Non-guarantee: legacy production paths are not yet universally routed through it; task roles and real identity proof remain future work.

## Phase 1: Control-plane convergence

Priority: Immediate

Difficulty: High

Dependencies: P0-1, P0-2, P0-3A

### Purpose

Make the modern core the only privileged production path before isolation or remote adapters increase the attack surface.

### Scope

- Define a mandatory capability dispatcher distinct from metadata-only `ToolRegistry`.
- Route the CLI/CUI through `ProjectRuntime` and `TaskService`.
- Replace direct privileged behavior in legacy `Kernel`, executor, coder, Git, orchestrator, evolution, and Ollama integration with typed service calls or mark it development-only and unreachable.
- Consolidate permission and path-policy ownership in the core.
- Introduce production guardrails/lint tests against unapproved `subprocess`, direct project writes, repository access, and provider calls.
- Establish one process as owner of the task/audit state or formalize safe IPC.

### Not in scope

- OS sandbox/provider implementation;
- remote API, MCP, Discord, GUI;
- autonomous evolution behavior;
- destructive Git rollback.

### Completion criteria

- End-to-end CLI tests prove task creation, authorization, execution request, result, and audit all pass through the core.
- No production adapter imports isolation/process implementations.
- `ToolRegistry` cannot be used as a dispatcher.
- Static searches and architecture tests identify all allowed subprocess and filesystem mutation sites.
- Legacy code is either migrated, isolated as explicit compatibility code, or removed only through a separately reviewed change.

### Security impact

Closes architectural bypasses. It does not yet contain a malicious allowed executable.

## Phase 2: Execution broker and truthful host controls

Priority: Immediate

Difficulty: High

Dependencies: Phase 1

### Purpose

Separate authorization from launch mechanics and make every attempt reproducible, bounded, and recoverable.

### Scope

- Immutable execution/result manifests with stable hashes.
- Broker/provider interface and attempt leases.
- Staged snapshot and validated artifact contract.
- E0 inspect-only and E1 trusted-host profiles.
- Windows Job Object process-tree, CPU, memory, active-process, timeout, and kill-on-close controls.
- Canonical executable resolution and identity recording.
- Effective-guarantee reporting, including explicit `NETWORK_NOT_ISOLATED` for E1.
- Broker crash/restart/cancel recovery.

### Not in scope

- Claim that host execution is a sandbox;
- general interpreters without high-risk policy and approval;
- container/VM networking;
- arbitrary package installation.

### Completion criteria

- Every execution has a task/attempt/manifest/authorization binding.
- Child and grandchild processes remain in the enforced process tree or launch fails closed.
- Resource and output limits have adversarial tests.
- Executable replacement and workspace-change races are detected or eliminated by staging/handles.
- Recovery never silently reruns an ambiguous attempt.

### Security impact

Strongly improves availability, cleanup, accountability, and process-tree control. Host filesystem and network escape remain acknowledged residual risks.

## Phase 3: Staged container execution

Priority: High; part of remote-capable MVP

Difficulty: High

Dependencies: Phase 2

### Purpose

Provide a practical containment profile for generated code, dependencies, tests, and builds that can run in Linux containers.

### Scope

- E3 provider using a supported Docker Desktop backend.
- Pinned image digests and an image admission policy.
- Non-root execution, no privileged mode/socket/devices, no network by default.
- Explicit CPU, memory, PID, time, and output limits.
- Content-addressed copy-in and patch/artifact copy-out; no writable source bind mount.
- Dependency-cache design that cannot write credentials or contaminate trusted caches.
- Capability and provider self-test at startup.

### Not in scope

- Windows-native GUI workloads;
- hostile multi-tenant workloads treated as fully solved by a shared-kernel container;
- arbitrary network access;
- Docker installation automation without user consent.

### Completion criteria

- Adversarial workloads cannot read selected host secrets, mutate the source repository, use the network, retain child processes, or exceed configured resource bounds in supported configurations.
- Provider reports backend and image digest and fails closed when expected controls are unavailable.
- Artifact application remains a separate authorization.
- Clean cancellation and crash cleanup are demonstrated.

### Security impact

Adds meaningful filesystem and network separation for the main generated-code workload, subject to container/backend trust and escape risk.

## Phase 4: Identity, authority, and audit hardening

Priority: High; required before remote access

Difficulty: High

Dependencies: Phase 1; can develop alongside Phases 2-3 but gates exposure

### Purpose

Replace actor-kind assertions and local unauthenticated approval with verifiable human, device, adapter, and worker authority.

### Scope

- Principal and project grant model for a single-user product.
- Human owner/approver/recovery authority, AI operator/proposer, APOS worker/system identities.
- Device pairing, credential storage, rotation, revocation, and step-up approval.
- Approval challenge bound to task/attempt/manifest/source/policy/expiry.
- Durable idempotency and replay store.
- Cross-process-safe audit writer, integrity chain, sequence, and protected export.
- Observer filtering and result disclosure levels.

### Not in scope

- enterprise RBAC, organizations, or broad delegation UI;
- approval by chat text alone;
- AI or system human-approval authority.

### Completion criteria

- Identity proof cannot be replaced by an `ActorKind` value supplied by a caller.
- Approval replay, cross-task use, stale manifest use, and revoked principal use fail closed.
- Audit detects removal/reordering/modification within the stated threat boundary.
- A compromised adapter service cannot mint human approval or worker identity.

### Security impact

Creates the minimum trustworthy authority base for remote control.

## Phase 5: Versioned local protocol and outbound relay

Priority: High; required for mobile value

Difficulty: High

Dependencies: Phases 1, 2, and 4; Phase 3 required before remote generated-code execution

### Purpose

Make durable local capabilities reachable without exposing a local command server.

### Scope

- Versioned task/status/approval/cancel/diff/artifact protocol.
- Loopback or OS-local IPC gateway with local authentication.
- Outbound TLS relay client, device/project pairing, leases, acknowledgements, expiry, and idempotency.
- Offline queue and reconnect semantics.
- Bounded progress and result streaming with redaction.
- Revocation and incident recovery.

### Not in scope

- generic remote method invocation;
- public direct access to `ProjectRuntime`, Ollama, subprocess, or filesystem endpoints;
- arbitrary binary artifact delivery without validation.

### Completion criteria

- No inbound laptop port is required for standard use.
- Replayed, reordered, stale, duplicated, and cross-project messages have deterministic safe outcomes.
- Cancellation and completion distinguish request, acknowledgement, and actual process termination.
- Relay outage does not corrupt task state or widen authority.

### Security impact

Adds a large remote attack surface but contains it behind authenticated, typed, durable operations.

## Phase 6: High-level AI adapter and local operator UI

Priority: MVP completion

Difficulty: Medium to high

Dependencies: Phases 1-5

### Purpose

Deliver the real user experience: ask from a mobile high-level AI, approve safely, run local work, and receive evidence.

### Scope

- Minimal MCP/ChatGPT adapter: submit, inspect, approve via APOS ceremony, cancel, get result/diff.
- Narrow tool schemas with project and capability allowlists.
- Local desktop or compact local UI for pairing, approval detail, task/recovery status, and audit export.
- Prompt-injection-aware rendering and explicit untrusted-output boundaries.
- End-to-end test/build and staged patch workflow.

### Not in scope

- arbitrary commands;
- Discord dependency;
- native mobile app;
- automatic remote Git push;
- autonomous self-evolution.

### Completion criteria

- A mobile user can request a registered local test on one project, approve it, monitor it, and receive a trustworthy result.
- A candidate patch can be staged, tested, reviewed, and separately applied.
- Compromised/replayed adapter calls cannot bypass local policy or approval.
- The workflow remains usable when the laptop disconnects and reconnects.

### Security impact

This is the first remotely useful product boundary. It is safe only to the degree proven by the preceding phases.

## APOS MVP boundary

MVP is complete at the end of Phase 6, with E1 limited to trusted host tools and E3 used for generated/untrusted Linux-compatible execution. The user can operate one registered project from a trusted high-level AI/mobile interface, with durable tasks, authenticated approval, isolation, monitoring, cancellation/recovery, diff/artifact review, and optional local checkpoint.

The MVP deliberately excludes general-purpose remote command execution. Its product value is a safe set of project workflows, not unlimited flexibility.

## Post-MVP milestones

### Native restricted worker and Windows VM profiles

- Purpose: cover Windows-native workloads that E3 cannot run.
- Scope: evaluate E2 dedicated low-privilege/AppContainer worker; later E4 Windows Sandbox or Hyper-V copy-in/copy-out provider.
- Dependency: stable broker contract and demonstrated workload demand.
- Completion: provider-specific containment tests and truthful capability reporting.
- Difficulty/security: very high integration cost; can add strong Windows coverage.

### Git state service

- Purpose: make local changes reviewable and recoverable.
- Scope: status/diff/revision first; isolated worktree; explicit local checkpoint; later separate fetch/push capability.
- Dependency: dispatcher, authority, staging/artifact model.
- Completion: dirty-tree preconditions, author/message policy, credential isolation, no force/destructive operations.
- Difficulty/security: medium; Git hooks, filters, credentials, and shared refs require care.

### Optional local model accelerator

- Purpose: handle narrow local/private implementation or summarization tasks.
- Scope: model adapter, pinned identity/config, bounded context/output, proposal-only results, evaluation and fallback.
- Dependency: dispatcher and isolation; never a prerequisite for core execution.
- Completion: measurable quality/latency/cost benefit on a defined benchmark without added authority.
- Difficulty/security: medium to high; prompt injection and local endpoint exposure remain.

### Additional adapters

- GitHub App: asynchronous issue/PR workflows and result presentation.
- Discord: notifications and constrained interactions only.
- Native mobile: only after stable protocol and demonstrated demand.
- Each adapter depends on the same gateway and cannot add capabilities privately.

### Controlled evolution

- Purpose: let APOS propose improvements to a clone/branch under external review.
- Scope: proposal generation, benchmark/evidence, isolated execution, signed artifact, human/Codex review, explicit integration.
- Dependency: all MVP controls plus mature Git service, reproducible environments, supply-chain controls, and independent evaluation.
- Exclusions: self-authorizing, self-deploying, self-modifying the running control plane.
- Security impact: very high; must remain outside the trusted runtime and fail closed.

## Git checkpoint timing

Git support is not a prerequisite for beginning execution isolation, but a read-only Git identity (`HEAD`, status, diff) is needed early to bind manifests to source state. Full checkpoint behavior should be introduced after staged artifacts and authority are stable.

Recommended sequence:

1. Before execution: record `HEAD` and a dirty-tree fingerprint; do not auto-commit user work.
2. During execution: operate on a copied snapshot or APOS-owned worktree.
3. After successful tests: present the candidate diff and evidence.
4. After explicit apply authorization: apply to the real working tree with race checks.
5. After validation and policy/approval: optionally create a local APOS checkpoint commit.
6. Only after a separate network/credential authorization: push a named non-protected branch.

Automatic commit may be allowed only for an APOS-owned branch/worktree, deterministic changes, clean preconditions, user-configured identity/message policy, and no hooks unless explicitly trusted. Never auto-force-push, hard-reset, clean untracked files, or rewrite history.

## Quality gates for every future milestone

- Threat model and trust-boundary update before implementation.
- Migration plan identifying all bypasses and compatibility paths.
- Unit, integration, crash/restart, concurrency, and adversarial tests proportional to the boundary.
- Effective-security self-test and fail-closed behavior on unsupported hosts.
- Documentation of what is and is not guaranteed.
- One review branch/checkpoint, independent code review, and explicit merge approval.
- No expansion of remote scope in the same change that first introduces a privileged backend capability.

## Priority summary

| Order | Milestone | User value | Security dependency |
|---:|---|---|---|
| 1 | Control-plane convergence | Reliable local foundation | Closes bypasses |
| 2 | Broker + E1 host controls | Durable bounded local tasks | Process/recovery control |
| 3 | E3 staged container | Safer generated code/tests | Filesystem/network isolation |
| 4 | Identity/authority/audit | Real human approval | Remote exposure gate |
| 5 | Local protocol + outbound relay | Mobile connectivity | Replay/device/project security |
| 6 | ChatGPT/MCP + local UI | APOS MVP | End-to-end safe UX |
| 7+ | Windows strong providers, Git, local model, adapters | Broader compatibility/capacity | Demand-driven hardening |
