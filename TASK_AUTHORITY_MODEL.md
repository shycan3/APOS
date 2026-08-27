# APOS Task Authority Model

Status: Future authorization design; no role system is implemented here.

## Purpose

P0-3A correctly separates human approval intent from deterministic system authorization and makes `TaskService` the official task control plane. The next problem is that the identity that creates a task is not necessarily the identity that should operate, observe, approve, execute, or recover it later.

This document separates those authorities. It does not grant them automatically and does not imply a multi-user product in the MVP.

## Core distinction

Five concepts must not be collapsed:

- Principal: an authenticated human, device, service, adapter, or AI session.
- Actor: the identity recorded as having requested or performed a particular action.
- Task role: a scoped relationship between a principal and one task/project.
- Permission: policy evaluation for a capability and resource.
- Approval: an authenticated human decision bound to a specific high-risk action.

Task creation proves only who submitted the initial request. It does not prove ownership of the project, authority to approve, or authority to execute.

## Roles

### Task owner

The owner is accountable for task intent and may normally:

- view the task and non-sensitive results;
- amend or cancel it before execution, subject to state and policy;
- delegate bounded operator or observer access when project policy allows;
- receive completion and recovery notices.

Ownership does not imply approval authority, execution authority, project administration, or access to secrets. An AI may be recorded as requester/creator but should not normally be the durable owner. For user-originated tasks, the authenticated human account should own the task; the AI session is the proposing actor.

### Task operator

The operator may perform lifecycle actions explicitly granted for a task:

- submit a prepared manifest;
- request approval;
- claim/launch when all independent conditions are met;
- cancel, pause where supported, or retry according to policy;
- attach evidence and propose artifacts.

An operator cannot approve its own privileged request unless it separately satisfies the approval-authority policy, and AI/system principals must never satisfy human approval. Disconnection of one operator must not orphan ownership.

### Task observer

The observer has read-only access to a filtered task view:

- lifecycle status and progress;
- approved public summaries;
- artifacts/diffs allowed by project policy;
- audit events within a granted disclosure level.

Observers cannot infer access to raw logs, local paths, environment values, secret-bearing artifacts, or other tasks. Status subscriptions must be revocable and scoped.

### Approval authority

Approval authority is held only by an authenticated human principal with a current project grant and sufficient authentication assurance. It permits approving a specific request, not operating the whole task.

An approval must bind:

- approver principal and authentication method/assurance;
- project, task, attempt, and operation/capability;
- immutable manifest or artifact hash;
- source revision and relevant policy version;
- exact scope, risk/effects, expiry, nonce, and one-time consumption state;
- approval ceremony/challenge ID.

Approval becomes invalid when a material field changes, the task leaves its expected state, policy requires stronger assurance, the grant is revoked, or the expiry passes.

### Execution authority

Execution authority belongs to a trusted APOS worker/service principal, not to an AI, adapter, or human UI. It permits the broker to claim an eligible attempt and invoke exactly the authorized isolation provider and manifest.

The worker must prove:

- it is an enrolled runtime for the target project/device;
- the task is in the expected state and atomically claimable;
- authorization is current and any approval was consumed exactly once;
- the manifest hash and provider capability match;
- no competing active attempt owns the lease.

Execution authority cannot alter the manifest or convert an approval into broader rights.

### Recovery authority

Recovery authority permits resolving ambiguous durable state after crashes, lost workers, expired leases, partial artifact collection, or uncertain Git operations. It is intentionally distinct because recovery can discard work, terminate processes, or choose whether to retry.

Recovery actions include:

- inspect provider and process state;
- mark a stale attempt failed or abandoned;
- adopt a verified still-running operation where supported;
- terminate residual resources;
- collect/quarantine artifacts;
- authorize a fresh attempt with a new manifest/approval;
- resolve an uncertain checkpoint or remote synchronization result.

Automated recovery may perform only deterministic, non-destructive reconciliation. Retrying privileged or non-idempotent work requires policy and often human approval. A human project administrator should hold final recovery authority.

## Supporting roles

### Project administrator

Registers projects, pairs devices/adapters, assigns project roles, configures policy, revokes access, and handles recovery escalation. It is not implicit in task ownership.

### Adapter service

Authenticates external principals and submits normalized commands. It has no inherent project or approval authority. Its own service identity is recorded separately from the end-user principal.

### AI proposer

Creates plans, task proposals, patches, or tool arguments. Its output is untrusted input. It may receive operator rights for low-risk proposal transitions but cannot become approval authority.

### APOS system principal

Performs deterministic lifecycle and bookkeeping transitions explicitly allowed by code and policy. A system principal may record authorization outcomes and recovery facts but cannot manufacture human intent.

## Capability matrix

`Y` means the role may be eligible; every action still requires project scope, state validity, permission, and other conditions.

| Action | Owner | Operator | Observer | Approver | Execution worker | Recovery authority |
|---|---:|---:|---:|---:|---:|---:|
| View filtered status | Y | Y | Y | Y | Limited | Y |
| Amend draft task | Y | Y | No | No | No | No |
| Request approval | Y | Y | No | No | No | No |
| Grant human approval | No | No | No | Y | No | No |
| Claim and launch | No | Request only | No | No | Y | No |
| Cancel request | Y | Y | No | No | Execute cancellation | Y in recovery |
| Apply artifact | Policy | Y if granted | No | May approve | Executes exact operation | Recovery only |
| Create Git checkpoint | Policy | Y if granted | No | May approve | Executes exact operation | Resolve ambiguity |
| Retry failed attempt | Policy | Y if granted | No | May reapprove | Claims new attempt | Y |
| Resolve `RECOVERY_REQUIRED` | No | No | No | May approve effects | Performs mechanics | Y |
| Delegate task view/control | Y if policy | No | No | No | No | Admin override |

No single `Y` is sufficient by itself.

## Authority records

Future persistence should include:

### Principal

- stable principal ID;
- principal type: human, AI session, adapter service, runtime device, worker;
- issuer and subject for federated identity;
- authentication strength and session/device ID;
- status and revocation time.

### Project grant

- principal, project, role/capabilities, constraints;
- issuer and grant provenance;
- not-before, expiry, and revocation;
- policy version.

### Task role binding

- task, principal, role, grantor, scope;
- creation/expiry/revocation;
- whether inherited from a project grant or delegated.

### Operation authorization

- actor, effective roles, capability, resource;
- decision, policy version, and explanation;
- approval requirement and consumed approval;
- request/manifest hash.

Identity evidence should be referenced by immutable IDs; access tokens and raw authentication assertions must not be persisted in task or audit records.

## State transition authority

The task state machine remains the source of valid transitions. Authority adds a second requirement:

```text
valid transition
AND authenticated principal
AND task/project role
AND capability permission
AND required approval
AND concurrency/lease precondition
= allowed action
```

Recommended examples:

- `DRAFT -> PENDING_APPROVAL`: owner/operator plus valid immutable proposed operation.
- `PENDING_APPROVAL -> APPROVED`: only approval consumption tied to an authenticated human decision; no general repository transition.
- `APPROVED -> RUNNING`: execution worker atomically claims; an operator cannot directly set `RUNNING`.
- `RUNNING -> COMPLETED/FAILED`: owning worker with matching attempt/lease and verified result.
- `RUNNING -> RECOVERY_REQUIRED`: deterministic startup reconciliation or lease failure.
- `RECOVERY_REQUIRED -> terminal/new attempt`: recovery authority, with fresh approval when effects may be repeated.

## Delegation rules

- Delegation can narrow but never expand the grantor's effective scope.
- Approval authority is not delegable by a task owner unless project policy explicitly grants it to the receiving human.
- AI and system principals cannot receive human approval authority.
- Adapter service credentials cannot stand in for end-user identity.
- Delegation has explicit expiry and is immediately revocable.
- A disconnected AI session can be replaced as operator without changing task ownership or existing evidence.
- Cross-project delegation is forbidden; project identity is always explicit.

## Long-running task semantics

An operator session may disconnect while execution continues. Therefore:

- task and attempt leases belong to worker identities, not client connections;
- the task owner persists independently of an AI conversation;
- observers can reconnect and resume from event sequence numbers;
- cancellation uses a durable command and acknowledgement;
- approval remains bound to the attempt, not the conversation;
- a replacement operator can monitor but cannot inherit private conversation context or unstated authority;
- retry always creates a new attempt ID and evaluates whether prior approval still applies. Default: it does not.

## Recovery examples

### AI connection disappears before execution

The task remains owned by the human, with the AI recorded as proposer/operator. It may wait for approval, be cancelled by the owner, or receive a new operator. No recovery privilege is needed.

### APOS restarts while a task was running

Startup marks the attempt `RECOVERY_REQUIRED` unless the provider offers verified reattachment. A recovery authority inspects evidence. APOS never assumes the command failed and automatically repeats it.

### Human needs to recover an AI-created task

The human must have project recovery authority. The task's creator identity does not block recovery, and recovery does not rewrite historical ownership/actor records.

### Another trusted adapter requests status

The adapter authenticates its user and obtains an observer/operator decision for that exact task/project. Being a registered adapter alone gives no task visibility.

## Approval authentication requirements

The current local unauthenticated human boundary must remain fail-closed for remote exposure. Future authenticated approval requires:

- a verified human account, not actor-kind metadata;
- recent authentication appropriate to risk, with step-up for high-risk operations;
- challenge binding to task/attempt/manifest and an explicit approval action;
- anti-CSRF/state protections for browser flows;
- device/session context and phishing-resistant methods where practical;
- expiry, one-time consumption, revocation, and audit;
- no approval based only on possession of an adapter bot token.

## Compatibility with P0-3A

P0-3A's design choices should be preserved:

- `TaskService` stays the official control plane.
- The repository remains private by convention and should become inaccessible outside the service boundary in future packaging.
- General transitions cannot enter `APPROVED` or `RUNNING`.
- `ApprovalGrant` continues to represent human-originated intent only.
- `PermissionDecision` remains separate from human approval and system lifecycle semantics.
- `AUTHENTICATED_HUMAN` remains fail-closed until identity proof exists.

The future role model extends these semantics; it does not replace them with a permissive ACL check.

## Minimum viable authority model

For a single-user MVP, avoid a complex organization RBAC system. Implement only:

- one authenticated human project administrator/owner;
- one paired local APOS device and execution worker;
- authenticated high-level AI/adapter sessions as proposers/operators;
- observer views derived from the same human account;
- explicit one-time human approval;
- deterministic system lifecycle authority;
- human recovery authority.

Keep the data model capable of multiple principals, but do not expose delegation or team management until there is a concrete use case.

## Invariants to test before remote access

- A task creator cannot approve solely because it created the task.
- AI/system/adapter service principals cannot create human approval evidence.
- A valid approval for one attempt, manifest, or project fails for every other one.
- Disconnect/reconnect does not transfer ownership or approval.
- Observer access never permits mutation and filters secret-bearing output.
- Only the execution worker can atomically enter `RUNNING`.
- Stale workers cannot complete or publish results after lease loss.
- Recovery does not retry non-idempotent work without a new decision.
- Principal, adapter, device, and actor identities remain distinguishable in audit records.
