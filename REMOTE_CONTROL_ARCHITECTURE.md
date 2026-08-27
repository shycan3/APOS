# APOS Remote Control Architecture

Status: Recommended future design; no remote interface is implemented by this document.

## Decision

APOS should not expose `ProjectRuntime`, an Ollama endpoint, or a generic local HTTP command API directly to the Internet. The recommended architecture is:

```text
Mobile user / ChatGPT
        |
        v
Cloud control service or supported connector
        |  authenticated task intent, status, approval challenge
        v
Durable relay queue
        ^
        |  outbound TLS connection initiated by the local machine
Local APOS relay client
        |
        v
Protocol gateway -> TaskService -> Authorization -> Dispatcher
                                      |
                               execution broker
```

The local APOS process initiates the connection outward, leases signed messages, and returns signed status/results. The cloud side never receives a raw shell, arbitrary local URL, filesystem handle, or provider credential. Human approval is a separate authenticated act bound to an immutable task attempt and manifest hash.

MCP is a suitable future AI-facing protocol adapter, not the remote security architecture itself. Discord is an optional notification and interaction surface, not an authority source. A desktop UI is the preferred local administration, approval, and recovery surface after the backend control path is stable.

## Design principles

- Protocol, transport, identity, authority, policy, and execution are separate layers.
- The local runtime makes the final decision using local project policy and durable state.
- Remote requests are untrusted proposals even after transport authentication.
- No inbound port is required on the user's laptop for the normal architecture.
- Every message is versioned, size-limited, idempotent, expiring, and bound to one project.
- Approval cannot be represented by an AI-authored message or a boolean in a task request.
- Status and output are least-privilege views filtered for the requesting observer.
- Cancellation is an authorized request, not proof that a process has stopped.
- Offline operation queues durable intent; it never silently widens authority or repeats non-idempotent work.

## Required protocol layers

### External request envelope

A request should contain:

- protocol version and message type;
- request ID and idempotency key;
- source adapter and authenticated principal;
- project ID, task ID if known, and expected project revision;
- requested capability and typed arguments;
- requested execution profile, not a provider-specific command;
- creation time, expiry, nonce, and parent message ID;
- signature or channel-bound authentication evidence;
- optional user-visible intent, clearly separated from structured fields.

The gateway rejects unknown fields where ambiguity is dangerous, stale messages, reused nonces, duplicate requests with different bodies, and project or principal mismatches.

### Internal command envelope

External identities are mapped to stable internal principals and roles before `TaskService`. Transport claims are not copied directly into `Actor`. The normalized command records:

- authenticated principal and authentication strength;
- effective task role and granted project scope;
- requested state transition or capability;
- adapter correlation metadata;
- policy and schema versions;
- whether human approval is required and which approval ceremony can satisfy it.

### Result envelope

Results should distinguish:

- accepted, rejected, awaiting approval, queued, running, cancelling, completed, failed, and recovery-required;
- requested versus effective isolation guarantees;
- bounded logs versus structured findings;
- source revision versus artifact base revision;
- generated patch/artifacts versus applied project changes;
- redacted public summary versus privileged local detail.

## Candidate comparison

| Candidate | Mobile UX | Connectivity and offline | Authentication/security | Approval, monitoring, presentation | Recommendation |
|---|---|---|---|---|---|
| Discord bot | Good notifications and buttons; familiar mobile app | Gateway is outbound-capable; cloud dependency | Bot token and channel membership are not sufficient proof of project authority; message content and compromised accounts are risks | Useful status/buttons, limited structured diff and recovery UX | Optional adapter after the secure gateway; never primary control plane |
| MCP | Excellent fit for AI tool discovery and typed calls | Usually requires a reachable remote server; local STDIO does not solve mobile reachability | HTTP MCP needs OAuth, token audience validation, secure storage, PKCE, and replay controls; prompt injection remains | Tool approval can fit, but APOS must perform its own authenticated approval and audit | Preferred AI protocol after authority and gateway exist |
| GitHub Issues/PRs | Excellent mobile and asynchronous collaboration | Works while laptop is offline; local client can poll or consume events later | GitHub App offers fine-grained repo permissions and short-lived installation tokens; repository content remains untrusted | Strong diff/review/results; weak for immediate local process control | Good early asynchronous adapter for Git-centric tasks |
| Local HTTP API + tunnel | Flexible and easy to prototype | Requires laptop online and tunnel lifecycle | Highest accidental RCE risk; tunnel auth does not replace APOS auth; broad endpoint exposure and SSRF risks | Custom UI possible, but long-running state must still be durable | Reject direct tunneling of runtime endpoints; use only behind a hardened relay gateway |
| Desktop GUI | Best local approval, recovery, and secret-safe detail | Local only; usable offline | Can use OS session/local IPC; smaller remote attack surface | Excellent for approvals, process status, diffs, and recovery | Recommended local administration surface, not remote transport |
| Native mobile app | Potentially best dedicated UX | Requires cloud push/relay anyway | Full account, device, key, update, and recovery lifecycle | Can be excellent but expensive | Defer until protocol and product demand stabilize |
| ChatGPT connector/plugin | Natural high-level AI and mobile conversation UX | Requires a reachable authenticated service | Inherits MCP/connector and prompt-injection concerns; approval UX cannot be assumed to equal APOS human proof | Strong reasoning and conversational monitoring | Strategic target through a secure cloud-to-local relay |
| Outbound APOS relay | Neutral transport usable by several clients | Laptop connects outward; queue supports offline intent and resumable status | Device key, mTLS or equivalent, signed/expiring messages, replay store, project scopes | Enables durable monitoring and separate approval ceremonies | Recommended connectivity foundation |

## Recommended end-to-end mobile flows

### Run tests

1. The user asks ChatGPT to run a registered APOS test capability.
2. ChatGPT proposes a typed task through an MCP/connector adapter.
3. The cloud gateway authenticates the user and stores an expiring request.
4. The local relay client leases the request over an outbound connection.
5. APOS maps identity, validates project/revision/capability, and creates a task.
6. If policy requires approval, APOS returns an approval challenge containing human-readable effects and the manifest hash.
7. An authenticated user approves through the supported remote ceremony or local desktop UI.
8. APOS consumes the one-time approval, claims the task, runs the minimum isolation profile, and records progress.
9. The user sees bounded progress and a final result. Raw untrusted output is clearly separated from APOS status.

### Fix a bug

1. The high-level AI inspects authorized project context and proposes a patch or a bounded local-model subtask.
2. APOS stages the patch without modifying the real project.
3. Tests run in a staged execution profile.
4. APOS returns a diff, test evidence, source revision, and artifact hashes.
5. Applying the patch is a separate authorization and, when required, human approval.
6. A local Git checkpoint may follow successful validation under another explicit capability.
7. Remote push is not implied by checkpoint creation.

## Discord assessment

### What Discord is good for

- push notifications that a task needs approval or recovery;
- concise status and completion summaries;
- explicit slash commands mapped to narrow capabilities;
- buttons that initiate, but do not themselves prove, an APOS approval ceremony;
- a convenient prototype UI for one user.

### What Discord must not do

- accept raw shell command text;
- infer authority from display name, channel name, or message content;
- store APOS secrets, Git credentials, local paths, or full logs;
- permit a bot token to create human approvals;
- directly call execution or filesystem services;
- treat edited, replayed, quoted, forwarded, or AI-generated messages as trusted intent.

Discord HTTP interactions require request-signature validation, and Gateway interactions still require mapping Discord identity to an APOS principal. The official Discord documentation requires signature verification for HTTP interactions and webhook events: [Interactions](https://docs.discord.com/developers/platform/interactions) and [Webhook Events](https://docs.discord.com/developers/events/webhook-events).

### Judgment

Discord is not required for the APOS MVP. Add it only after the relay, identity mapping, authority model, idempotency, and approval ceremony exist. Its role should remain notification and constrained interaction.

## MCP and ChatGPT assessment

MCP solves tool discovery, schemas, and calls between a model-facing client and an APOS adapter. It does not solve local-machine reachability, OS containment, task ownership, or human authentication.

An APOS MCP server must:

- expose coarse, intention-level tools such as `submit_task`, `get_task`, `cancel_task`, `get_diff`, and `request_apply`, not `run_command`;
- route every call through the protocol gateway and task authority checks;
- use OAuth for remote HTTP transport with token audience binding and validation;
- reject token passthrough and client-asserted actor roles;
- use exact redirect URIs, PKCE, short-lived tokens, and secure local token storage;
- allowlist tools per principal and project;
- require APOS approval for sensitive actions even when a client has its own approval UI;
- treat MCP tool arguments and outputs as prompt-injection and data-exfiltration surfaces;
- audit tool discovery version, call arguments after redaction, caller, decision, and result.

The MCP authorization specification requires audience-bound tokens and forbids token passthrough; see [MCP Authorization](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization). OpenAI documents that remote MCP servers can send, receive, and act on data, recommends approvals for sensitive actions, and highlights prompt-injection risks: [OpenAI MCP server guide](https://developers.openai.com/api/docs/mcp) and [MCP and Connectors](https://developers.openai.com/api/docs/guides/tools-connectors-mcp).

### Judgment

MCP is strategically useful and is the preferred ChatGPT-facing adapter protocol, but it must come after the internal dispatcher, real identity/authority, execution isolation, and outbound relay. It is not itself P0-3B.

## GitHub assessment

GitHub is useful as an asynchronous task inbox and result surface for committed repositories:

- issue labels or commands can request a narrow registered workflow;
- comments can present status and links to artifacts;
- pull requests naturally present diffs and review;
- the laptop may be offline when the request is created;
- a local APOS client can poll outbound, avoiding an inbound laptop endpoint.

Prefer a GitHub App over a broad personal access token. Grant only selected repositories and minimum permissions, use short-lived installation tokens, validate webhook signatures and delivery IDs, and never execute issue/PR text as commands. GitHub recommends minimum app permissions and webhook secret validation: [Choosing permissions](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app), [App best practices](https://docs.github.com/en/enterprise-cloud@latest/apps/creating-github-apps/about-creating-github-apps/best-practices-for-creating-a-github-app), and [Validating webhook deliveries](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries).

GitHub cannot represent all local truth: uncommitted state, local-only files, device tests, and approval authority remain APOS concerns. An issue author who can write repository content is not automatically authorized to operate a local project.

## Local gateway and relay security

### Local endpoint

- Bind only to loopback or use named pipes/Unix-domain sockets where available.
- Authenticate the local client even on loopback; other local processes are not inherently trusted.
- Do not share the Ollama port or runtime object over the same endpoint.
- Expose typed task operations, not arbitrary method dispatch.
- Enforce body, field, output, concurrency, and rate limits.
- Use a process owner lock so one service owns the task database and audit sink.

### Outbound relay

- Register each APOS installation as a device with a rotatable private key stored in OS-protected storage.
- Bind cloud accounts to device and project grants through an explicit pairing ceremony.
- Use TLS and preferably mutual device authentication or signed channel-bound messages.
- Lease messages with acknowledgements; do not delete until APOS durably accepts or rejects them.
- Store replay IDs/nonces and deterministic idempotency outcomes.
- Make messages expire and require reauthorization after material task/manifest changes.
- Encrypt sensitive result payloads for the intended principal where the relay should not read them.
- Support device revocation, project unpairing, key rotation, and incident audit export.

### Offline behavior

- New remote tasks may remain queued until the device reconnects.
- Approvals expire; reconnect does not extend them automatically.
- Running local tasks may continue according to local policy, but remote status reports are delayed.
- Cancellation requested while offline is not complete until the local broker acknowledges termination.
- Duplicate delivery returns the stored idempotent outcome and never re-executes a completed attempt.

## Approval UX

An approval screen must identify:

- authenticated human account and device/session assurance;
- project display name and stable project ID;
- task goal and exact attempt;
- source revision and dirty-state summary;
- executable/tool, arguments summary, isolation profile, filesystem and network effects;
- whether code, patch, Git state, remote systems, or credentials may change;
- expiry, one-time scope, and manifest fingerprint;
- clear approve and deny actions with no preselected approval.

A chat message saying "approved" is not enough unless the adapter turns it into an authenticated, challenge-bound ceremony that APOS can independently verify.

## Result presentation and prompt safety

- Separate APOS-authored status from subprocess/model/repository content.
- Mark raw logs and diffs as untrusted.
- Do not render arbitrary remote URLs or images from tool output without domain policy.
- Redact secrets before persistence and again before remote release.
- Provide hashes and truncation markers for omitted output.
- Never let text in logs or repository files invoke tools without a fresh model decision and APOS authorization.

## Delivery sequence

1. Migrate the local CLI to the modern runtime and mandatory dispatcher.
2. Implement the task authority and authenticated local approval boundary.
3. Implement execution isolation and durable broker recovery.
4. Define and test the versioned remote protocol with a local fake transport.
5. Build outbound relay and device/project pairing.
6. Add a minimal ChatGPT/MCP adapter for task/status/diff operations.
7. Add GitHub App integration if repository-native asynchronous work is valuable.
8. Add desktop UI for richer local administration and recovery.
9. Add Discord only as an optional constrained adapter.

## Remote readiness gate

APOS is not ready for untrusted remote AI access until all are true:

- every privileged path uses the dispatcher and authority checks;
- human authentication and challenge-bound approval are implemented;
- replay/idempotency/project scoping are durable;
- at least one appropriate isolation profile is enforced and honestly reported;
- audit is cross-process safe and tamper-evident enough for the threat model;
- secrets, logs, artifacts, and model outputs have tested redaction and size limits;
- adapter compromise cannot mint approvals or invoke providers directly;
- incident revocation and recovery behavior are tested.
