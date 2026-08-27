# APOS Future Threat Model

Status: Forward-looking extension of `SECURITY_THREAT_MODEL.md`

Scope: architecture proposed after P0-3A; not a claim that future controls exist today

## Boundary statement

Today APOS provides a logical project boundary through selected core APIs, not a true OS sandbox and not a safe network-facing control plane. The future architecture adds adapters, identity, workers, containers, Git operations, local models, and remote messages. Each addition creates a new boundary that must be independently authenticated and enforced.

The primary protected boundary remains the local project runtime and host. Remote AI, adapters, repository content, generated code, subprocess output, local-model output, packages, images, and relay payloads are untrusted.

## Assets

- source, untracked work, build artifacts, and project availability;
- APOS task/approval/audit state and policy;
- human, device, adapter, worker, Git, cloud, and model credentials;
- host files, browser/profile data, SSH keys, tokens, registry, services, devices, and network identity;
- execution manifests, patches, result artifacts, logs, and provenance;
- user intent, approval evidence, and project-role grants;
- Git history, refs, remotes, hooks, configuration, and signing identity;
- relay queues, encryption keys, and idempotency/replay records.

## Actors and trust

| Actor | Trust level | Allowed role |
|---|---|---|
| Authenticated human | Trusted for explicit scoped decisions, not immune to phishing | Owner, approver, administrator, recovery authority |
| High-level AI | Untrusted proposer with useful reasoning | Propose/operate within granted task scope |
| Local LLM | Untrusted local dependency and output generator | Narrow proposal-only resource |
| Adapter | Trusted only for verified transport mapping | Authenticate/normalize/present; no policy or approval power |
| APOS gateway/runtime | Trusted computing base | Durable control plane and enforcement |
| Execution worker/provider | Limited trusted service, exposed to hostile workloads | Execute exact manifests and return evidence |
| Repository/dependencies | Untrusted data and code | Inputs to inspect/build/test |
| Relay/cloud service | Honest-but-compromiseable transport/store | Deliver authenticated envelopes, not authorize local effects |

## Threat register

The "current defense" column refers to P0-3A unless a future prerequisite is named. Proposed controls are not current guarantees.

| Threat | Attack path | Current defense | Remaining risk | Recommended future control |
|---|---|---|---|---|
| Remote adapter compromise | Stolen service credentials or code change submits forged tasks, reads results, or calls a backend directly | No production remote adapter; core authorization exists only when used | Future adapter may bypass `TaskService` or impersonate users | Thin adapters, mandatory gateway/dispatcher, separate service and user identities, least privilege, signed/versioned messages, revocation, adapter conformance tests |
| Discord token theft | Attacker controls bot, sends messages, edits status, or harvests result data | None because Discord is not implemented | Bot identity could be mistaken for human approval or project access | Notification-only default, narrow slash commands, Discord user-to-principal mapping, no raw commands/secrets, independent approval challenge, token rotation and incident revocation |
| MCP client impersonation | Forged bearer token, token passthrough, malicious client registration, or wrong-audience token calls APOS tools | No MCP implementation | Remote caller could gain operator/observer rights | OAuth 2.1 practices, audience-bound access tokens, PKCE, exact redirect URIs, short expiry, secure storage, no token passthrough, project-scoped tool allowlists |
| Local API exposure | Service binds all interfaces or tunnel exposes generic methods; CSRF/SSRF/local malware invokes it | No API server; therefore no exposure yet | A prototype endpoint could become direct RCE | Loopback/named-pipe binding, local client auth, typed endpoints, origin/CSRF defenses, request limits, no generic dispatch, no direct tunnel; outbound relay instead |
| Replay attack | Captured submit/approve/cancel/apply envelope is resent after reconnect or against another attempt | One-time persistent approval consumption protects exact current approval semantics | No general remote nonce/idempotency store or channel binding | Signed expiring envelopes, durable nonce/idempotency table, task/attempt/project/manifest binding, deterministic duplicate results, sequence and clock-skew policy |
| Task hijacking | Another AI session/adapter claims, changes, observes, cancels, or completes a task | Atomic task claim and lifecycle restrictions | Caller identity equals actor by convention; no durable owner/operator model | Principal/project grants, task role bindings, worker leases, observer filtering, delegation/revocation rules, owner independent of AI session |
| Approval replay | Grant is copied to another operation, task, attempt, revision, or changed manifest | P0-3A request binding, one-time consumption, and human-origin semantics | No real human authentication; future remote serialization may omit fields | Authenticated challenge binding all material fields, expiry/nonce/policy/source hash, atomic consume with claim, invalidate on mutation, step-up auth |
| Git credential exposure | Git subprocess reads credential helper, SSH agent/key, environment, remote URL token, config, hooks, or logs | Environment sanitation covers selected execution paths | Legacy Git paths and trusted executables retain host access; logs can leak | Dedicated Git service, no credentials in argv/URL/logs, isolated credential broker, sanitized config/env, disabled hooks by default, narrow app tokens, redaction tests |
| Local LLM prompt injection | Repository/log instructs local model to leak context, emit dangerous tool calls, or alter task semantics | Model output is not yet the core control plane; some legacy integration exists | Local model may be treated as trusted coder and its output fed to execution | Proposal-only model adapter, content provenance labels, structured schema validation, no model authority, context minimization, output bounds, independent patch/test review |
| Malicious repository content | README/instructions, config, hooks, test discovery, build scripts, filenames, symlinks, or parser payloads influence AI or tools | Logical workspace and secret policy for core file APIs | Trusted test runner/interpreter can execute repository code with host rights; prompt injection reaches AI | Treat repo as hostile, disable hooks, staged execution, no host-write mount, safe parsers, filename/path hardening, tool-specific risk labels, prompt-injection boundaries |
| Malicious generated code | AI/local model writes code that reads host data, spawns children, exfiltrates, persists, or damages source | Authorization and controlled host subprocess limits | E1 cannot stop host filesystem/network/API access | Default E3 staged container for generated code, no network, non-root, resource/PID limits, immutable source copy, artifact verification, E4 for high-risk Windows code |
| Dependency supply-chain attack | Package install/build script or compromised image executes code, poisons cache, steals credentials, or changes output | Command policy/approval can flag package managers | No enforced network or image/package provenance; host package managers are launchers | Locked dependencies, pinned image digests, signed/provenance-aware artifacts where available, isolated caches, deny network by default, allowlisted proxy, SBOM and audit |
| Container escape | Kernel/runtime vulnerability or privileged configuration reaches host | Containers not implemented | Shared-kernel containers are not a robust hostile multi-tenant boundary | Patched runtime/backend, rootless/non-root where supported, no privileged/socket/devices, seccomp/capability limits, hypervisor backend/VM profile for higher risk, incident teardown |
| Network exfiltration | Process, package manager, DNS, local model, plugin, or dependency sends source/secrets outward | `NetworkPolicy` is declarative only | Current host execution has normal network access | Enforced no-network provider default, narrow audited egress proxy, destination/method policy, DNS control, byte/time limits, no ambient proxy credentials |
| Log poisoning | Process emits forged APOS-like status, terminal control sequences, secrets, huge/binary output, or prompt instructions | Output bounds and redaction exist in controlled execution/audit paths | UI/AI may confuse untrusted output with trusted status; cross-process audit integrity is limited | Typed event channel separated from stdout/stderr, escaping and control-character filtering, source labels, hash/truncation, redaction before storage/release, prompt-safe rendering |
| Worker impersonation | Malicious local process reports fake completion/artifacts or claims tasks | No worker identity protocol today | Local malware or adapter could forge provider results | Enrolled worker keys, authenticated IPC, lease and manifest-hash binding, result signatures/MAC, process-owner lock, reject stale workers |
| Artifact substitution | Patch/artifact changed between validation, approval, collection, and application | Some resource binding and workspace resolution exist | TOCTOU across paths and mutable files remains possible | Content-addressed artifacts, immutable staging, open-handle/canonical checks, apply exact hash, source revision/dirty-state precondition, atomic writes |
| Executable substitution | PATH/PATHEXT, shim, symlink/reparse point, or binary update changes what runs after authorization | Command policy and `shell=False`; environment sanitation | Resolution and launch can differ; allowed interpreter remains arbitrary-code capable | Canonical absolute executable, trusted-root policy, file identity/hash/signature, open/launch race mitigation, no current-directory/PATH lookup for privileged tools |
| Workspace TOCTOU | Junction, symlink, reparse point, rename, or concurrent process changes a resource after resolve/authorize | Canonical logical resolve checks | Host operation may follow changed object; subprocess can mutate concurrently | Staged immutable snapshots, reject reparse points where unsupported, handle-relative operations, final-path verification, source digest and apply-time race checks |
| Approval phishing/confusion | UI hides args/network/source changes or attacker overlays/redirects approval | P0-3A can bind approval data but lacks identity/UI | Human may approve a different effect than expected | Trusted approval surface, explicit effects, manifest fingerprint, step-up auth, exact redirect/state/CSRF protection, no preselection, material-change invalidation |
| Result data disclosure | Observer receives raw logs, local paths, secrets, source, or artifact outside grant | Core redaction exists in audit/execution paths | No observer model; legacy paths and novel encodings can leak | Disclosure levels, least-privilege result views, double redaction, artifact access grants, expiry, download audit, encoding/binary tests |
| Denial of service | Task flood, fork/process bomb, huge output, disk fill, queue flood, model VRAM exhaustion | Timeout/output limits and atomic claim | Host process tree/resource/disk/network not comprehensively bounded | Admission/rate/concurrency quotas, Job Objects, container limits, disk quotas, bounded queue/artifacts, backpressure, per-principal budget and cancellation |
| Crash ambiguity | APOS dies after external effect but before state/audit commit; restart repeats work | P0-3A maps running tasks to recovery-required | Process/provider/Git/remote side effect may remain uncertain | Attempt leases, provider reconciliation, write-ahead intent/result records, idempotent operations, no automatic repeat, human recovery for ambiguous effects |
| Audit tampering | Subprocess edits/deletes/reorders logs or state; concurrent writers corrupt sequence | Append-oriented API and redaction resist accidental API mutation | Same-user malicious subprocess can access files; append-only is not tamper-proof | Separate low-priv worker, protected audit service/storage, hash chain with sequence/checkpoints, cross-process locking, remote/WORM export, verification tooling |
| Git history destruction | AI invokes reset/clean/rebase/force push or hooks mutate state | No modern Git capability boundary; legacy client exists | User changes/history/remotes can be damaged | Deny destructive commands, typed Git operations, clean/dirty preconditions, APOS worktrees, human approval, protected branches, no force, prefer revert proposals |
| Git ref/worktree race | Concurrent user/APOS operations change HEAD, index, refs, or shared worktree metadata | Current core does not own Git concurrency | Patch/checkpoint may apply to wrong base or disturb user work | APOS-owned linked worktree/branch, repo lock/lease, expected revision, status fingerprint, atomic ref updates, no direct source mutation by workers |
| Relay compromise | Cloud queue operator reads/modifies/reorders tasks/results or blocks cancellation | Not implemented | Relay could impersonate remote intent or leak project data | End-to-end signed envelopes, optional payload encryption, expiry/nonces, device-local authorization, minimized result content, transparency/audit, revocation |
| Secret-policy bypass through derived data | Tool reads allowed file that embeds token, crash dump, build artifact, Git history, or encoded secret | Named secret paths and redaction patterns | Path policy cannot classify all sensitive content | Content classification/size limits, project-specific deny rules, no arbitrary history/env access, egress controls, result review, incident secret rotation |
| Policy downgrade/config tampering | Repository changes APOS policy/tool metadata to permit execution | Policy services exist but trust location/version needs hardening | Project content may influence control-plane configuration | Store trusted policy outside untrusted project tree, sign/version it, require admin approval for expansion, bind policy version to authorization/approval |

## Remote control requirements

Before any remote AI can submit executable work:

- the local runtime must initiate outbound connectivity or use an equivalently hardened gateway;
- human, adapter, device, and worker identities must be distinct and revocable;
- messages must be authenticated, project-scoped, expiring, replay-resistant, and idempotent;
- adapters must call only the versioned gateway and cannot import execution/filesystem/Git internals;
- sensitive operations require APOS-owned approval, regardless of upstream AI/client approval UX;
- status/output disclosure must follow observer authority and redaction policy;
- a transport compromise must not grant execution or approval authority by itself.

## Local model requirements

- Keep local model servers loopback-only; Ollama local API has no authentication by default.
- Do not forward remote credentials or APOS state into model prompts.
- Pin model, template, context, and adapter version in task evidence.
- Treat tool calls, patches, summaries, and confidence as untrusted proposals.
- Bound context/output, sanitize logs, and prevent prompt content from crossing authority fields.
- Evaluate narrow tasks against deterministic tests and a high-level AI/human review path.
- Do not let a local model alter its own policy, runtime, model configuration, or execution profile.

## Repository and dependency requirements

- Assume test/build/config scripts are executable hostile content.
- Never run hooks, filters, package lifecycle scripts, or IDE tasks implicitly on the host.
- Use staged immutable inputs and separate artifact application.
- Pin toolchain images and dependencies where practical; record resolved versions/digests.
- Keep credentialed dependency retrieval separate from untrusted build execution.
- Scan and bound archives, symlinks/reparse points, special files, filenames, and extraction paths.
- Make source revision and dirty-state evidence part of approval and result verification.

## Isolation requirements

Security labels must describe effective controls:

- `HOST_COOPERATIVE`: Job Object/resource controls, but host filesystem/network remain reachable.
- `RESTRICTED`: staged low-privilege context; enumerate whether network and host handles are actually denied.
- `CONTAINER_STAGED`: no source write mount, no network by default, explicit limits; container escape remains residual.
- `VM_STAGED`: separate kernel, copy-in/out, network disabled; hypervisor and transfer channel remain trusted.

If a requested control cannot be established, launch fails closed or requires an explicitly approved lower profile. Silent downgrade is forbidden.

## Audit integrity requirements

- One logical writer or transactional sequence allocation across processes.
- Stable canonical serialization, monotonic sequence, prior-record hash, and periodic signed checkpoint.
- Intent record before external effects and result/reconciliation record afterward.
- Separate trusted APOS events from untrusted logs; never parse stdout as lifecycle state.
- Redact before persistence; record truncation and content hashes.
- Protect audit storage from workers and isolate it from the project tree.
- Verify chains at startup and export to an independent location for higher-assurance operation.
- Define honest limits: a fully compromised host administrator can still tamper with local evidence unless independently anchored.

## Residual risks after the recommended MVP

- Host E1 processes retain the user's filesystem and network access; use only for genuinely trusted tools.
- Container/runtime/kernel vulnerabilities can cross E3 boundaries.
- Human approval can be socially engineered or rushed.
- A high-level AI can be prompt-injected by repository or tool output and repeatedly propose harmful but policy-valid work.
- Supply-chain compromise can produce malicious yet correctly pinned artifacts if the trusted source itself is compromised.
- Endpoint malware running as the user may steal displayed data or interact with local clients.
- Audit integrity cannot survive total host compromise without an external trust anchor.
- Complex toolchains may require capabilities that weaken containment; effective guarantees must remain visible.
- Availability depends on laptop power, connectivity, disk, virtualization, and provider health.

## Security decision gates

### Can current APOS be safely exposed to an untrusted external AI over a network?

**NO.** There is no authenticated remote identity/authority boundary, current approval does not prove a human identity, legacy privileged paths have not converged on the core, network policy is declarative, audit is not protected from same-user subprocesses, and current execution is not an OS sandbox.

### Can current APOS provide a true OS-level project sandbox?

**NO.** `ProjectWorkspace` and `shell=False` constrain APOS API behavior and command parsing, not the OS token, filesystem namespace, network, or interpreter APIs.

### Can a trusted interpreter potentially escape the APOS logical project boundary?

**YES.** Once allowed to run under the current host process model, Python, Node.js, PowerShell, `cmd.exe`, package managers, Git hooks, or similar launchers can access resources permitted to the host user unless an external OS isolation mechanism prevents it.

## Reference basis

- Existing APOS analysis: `SECURITY_THREAT_MODEL.md`, `ARCHITECTURE_AUDIT.md`, `P0_2_IMPLEMENTATION.md`, and `P0_3A_IMPLEMENTATION.md`.
- MCP security and token binding: [MCP Authorization](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization).
- Model/tool prompt-injection and approval guidance: [OpenAI MCP and Connectors](https://developers.openai.com/api/docs/guides/tools-connectors-mcp).
- GitHub least privilege and webhook validation: [GitHub App best practices](https://docs.github.com/en/enterprise-cloud@latest/apps/creating-github-apps/about-creating-github-apps/best-practices-for-creating-a-github-app) and [Webhook validation](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries).
- Discord interaction verification: [Discord Interactions](https://docs.discord.com/developers/platform/interactions).
- Isolation details and limits: the official Microsoft and Docker sources listed in `P0_3B_ARCHITECTURE_DECISION.md`.
