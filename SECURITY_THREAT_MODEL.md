# APOS P0-2 Security Architecture Review / Threat Model Audit

## Review Basis

- Repository: `https://github.com/shycan3/APOS.git`
- Branch: `review/p0-3a`
- Base commit: `7c5265436fb94f8c852ccd4d7e9e398874b53284`
- P0-3A checkpoint: `741b19f16d0e944129d54d2a79e47a5dd0498e4c`
- Additional reviewed state: uncommitted P0-3A Review Fix working tree
- Scope: the P0-2 base, P0-3A checkpoint, and current review-fix changes
- Review type: static architecture and threat-model audit
- Code changes: P0-3A architecture/security semantics review fixes only

This review distinguishes three different statements throughout the document:

1. **Enforced guarantee**: the current implementation directly enforces the property for the stated code path.
2. **Logical or cooperative policy**: the property holds only when every caller uses the intended API and trusted code behaves as expected.
3. **Non-guarantee**: the operating system or architecture does not enforce the property against malicious code running under the APOS user account.

## Executive Finding

P0-2 establishes a useful **logical authorization layer** for the new `apos.core` services. P0-3A adds SQLite-backed persistent task, request, approval, one-time consumption, crash recovery, and concurrent execution-claim controls. Neither phase establishes an operating-system sandbox. In particular, `ControlledExecutionService` authorizes an executable path, but it does not confine what that executable can do after launch. A trusted interpreter such as Python can read or modify files outside the project, open network sockets, invoke shells, spawn children, access Windows APIs, and modify the project audit log with the full rights of the APOS host user.

The strongest current controls are request normalization, exact executable allowlisting at assessment time, `shell=False`, capability decisions, exact request-digest approval binding, output bounding, environment reduction, and best-effort process termination. The most important missing controls are OS-enforced filesystem and network isolation, authenticated human approval, tamper-evident audit storage, a mandatory dispatcher, process-wide and cross-process containment, and migration or quarantine of legacy privileged paths.

## Security Boundary Definition

### Logical project boundary

`ProjectWorkspace` defines a logical project root by resolving the registered root to a canonical path and deriving a project identifier from that path (`src/apos/core/workspace.py:82-93`). For a requested relative path, it:

- rejects empty, absolute, and lexical traversal paths;
- resolves the candidate path and checks that the result is below the registered root;
- applies a name-based secret-path policy before and after resolution; and
- optionally checks existence.

This boundary is enforced for operations that actually call `ProjectWorkspace.resolve()`, primarily `FileSystemService` and the working-directory selection in `ControlledExecutionService`.

The logical boundary is a **path-validation boundary at a point in time**. It is not a capability-secure file handle, an ACL boundary, a container mount boundary, or a Windows security-token boundary.

### OS-level process boundary

There is no OS-level process boundary in P0-2. `subprocess.Popen()` launches the trusted executable under the same Windows user token, integrity level, desktop/session, filesystem ACLs, registry access, network stack, and credential context as APOS (`src/apos/core/execution.py:359-371`). No restricted token, AppContainer, Windows Sandbox, container, VM, Job Object security limit, filesystem namespace, or firewall rule is applied.

Setting `cwd` to a project directory does not confine a process to that directory. A process can use absolute paths, `..`, drive-relative paths, UNC paths, Windows APIs, registry APIs, named pipes, COM, and child processes independently of `ProjectWorkspace`.

### Boundary conclusion

| Boundary | Current status | Meaning |
|---|---|---|
| APOS core file API path boundary | Partially enforced | Normal calls reject obvious absolute/traversal paths and resolved escapes at check time. |
| Secret-name boundary | Cooperatively enforced | `FileSystemService` hides configured names, but processes and legacy code can access them directly. |
| Executable selection boundary | Enforced at assessment time | The resolved executable path must be in the explicit trusted set. |
| Process behavior boundary | Not enforced | An allowed executable retains the host user's full OS capabilities. |
| Network boundary | Not enforced | `NetworkPolicy` is declarative and heuristic only. |
| Audit integrity boundary | Not enforced against malicious code | The log is a normal project file writable by the host user. |

## Assets

- Project source code, tests, configuration, Git history, branches, tags, and uncommitted work.
- Files outside the project that are accessible to the host user.
- Credentials in files, environment variables, Windows Credential Manager, SSH agents, Git credential helpers, browser profiles, cloud CLIs, and developer tooling.
- APOS policy state, approval state, task identity, actor identity, and project identity.
- Audit events in `.apos/audit/events.jsonl`.
- Host availability: CPU, memory, process table, disk space, file handles, and network capacity.
- Network-reachable services, including loopback services, LAN resources, cloud metadata endpoints, and the public internet.
- Integrity of trusted executables and their dependent DLLs, modules, plugins, configuration, hooks, and helper programs.

## Actors

| Actor | Intended trust | Current identity proof |
|---|---|---|
| `USER` | Human operator allowed to approve | None; caller constructs `Actor(ActorKind.USER, actor_id)`. |
| `SYSTEM` | Trusted APOS automation | None; P0-3A rejects it at the human-approval boundary, but the label itself is caller-created. |
| `EXTERNAL_AI` | Untrusted or semi-trusted remote model | Caller-supplied label only. |
| `LOCAL_LLM` | Local model with limited authority | Caller-supplied label only. |
| APOS host process | Trusted computing base | Runs with the interactive user's OS rights. |
| Trusted executable | Allowed launch target | Trusted by canonical path, not by behavior or immutable file identity. |
| Child process | Not separately modeled | Inherits rights from the launched process; no independent authorization. |
| Legacy APOS runtime | Existing trusted application code | Operates outside the P0-2 authorization gateway in multiple paths. |

Actor kind and `actor_id` are data, not authenticated principals. They become security-relevant only after an adapter derives them from a verified identity and prevents clients from supplying or changing them.

## Trust Model

### Explicit trust assumptions in the current code

- The host Python process and imported APOS modules are trusted.
- The configured `PermissionPolicy` is trusted and available.
- Every privileged caller voluntarily uses the new core services.
- Every path in `CommandPolicy.trusted_executables` is trusted in full.
- Trusted executable files and their load dependencies do not change between validation and launch.
- A `USER` label on an `ApprovalGrant` represents actual local human intent, even though no identity proof exists.
- The process cannot or will not bypass `NetworkPolicy` using its own APIs.
- Other processes do not tamper with the audit file.

Several of these assumptions are false for an untrusted external AI or adversarial subprocess. Therefore the current design is suitable only as a local logical control layer, not as a hostile-code containment boundary.

### Policy granularity

`StaticPermissionPolicy` decides only by `Capability` (`src/apos/core/permissions.py:152-168`). It does not itself distinguish actor, resource, operation, risk level, command arguments, project state, time, or remote session. Those fields are included in a request digest, but they do not affect a static allow rule. An `ALLOW` rule for `PROCESS_EXECUTE` therefore allows that capability for every actor passed to the same engine, subject only to command assessment.

## Threat Model

### Adversary capabilities

This review assumes an adversary may:

- control external-AI tool inputs, including paths, executable names, arguments, environment overrides, request IDs, and declared network policy;
- influence project files, test code, Git configuration, hooks, dependency metadata, scripts, and modules imported from the project;
- cause an approved interpreter to execute adversarial project code;
- race filesystem objects using another process running as the same user;
- restart APOS or create multiple runtime instances;
- observe tool responses and approval-request metadata; and
- exploit any legacy CLI path exposed by a future adapter.

The adversary is not assumed to have already compromised the APOS Python process. If it has arbitrary Python execution inside the APOS host process, all Python-level controls are bypassable by definition.

### Principal threats

| ID | Severity | Threat |
|---|---|---|
| T1 | Critical | A trusted interpreter executes arbitrary code with the APOS user's OS rights. |
| T2 | Critical | A process declared `NETWORK_DENIED` accesses the network through an unrecognized path. |
| T3 | Critical | A remote client forges `USER`/`SYSTEM` actor data and constructs a valid approval grant. |
| T4 | Critical | A future adapter bypasses core authorization and calls legacy/direct privileged code. |
| T5 | High | A subprocess deletes, truncates, rewrites, or forges audit events. |
| T6 | High | Symlink, junction, reparse-point, cwd, target, or executable TOCTOU escapes a validated path. |
| T7 | High | Child or detached processes survive timeout/cancellation and continue operating. |
| T8 | High | Git, package-manager, loader, hook, or helper behavior turns an allowed command into code execution. |
| T9 | High | Same-user processes race approvals, project operations, or audit writes across runtime instances. |
| T10 | Medium | Sensitive information leaks through unmodeled stores, process output patterns, arguments, or external services. |
| T11 | Medium | Resource exhaustion occurs because memory and process-count limits are declared but not enforced. |

## Attack Surfaces

### ProjectWorkspace and FileSystemService

`FileSystemService` performs resolve/check, authorization, audit, and then the filesystem operation (`src/apos/core/filesystem.py:43-230`). This is a meaningful logical gate for cooperative callers. Attack surfaces remain:

- path objects are re-opened by name after authorization rather than held as verified handles;
- parent directories, targets, symlinks, junctions, and other reparse points can change between checks and use;
- read size is checked with `stat()` before `read_text()`, allowing replacement between the two calls;
- atomic replacement protects write completeness, not project confinement;
- `create_parents` acts after authorization and can traverse a parent changed by another process;
- secret detection is name-based and does not classify arbitrary credential content; and
- any process or legacy APOS module can bypass `FileSystemService` and call filesystem APIs directly.

### ControlledExecutionService

`ControlledExecutionService` checks cwd, checks a command allowlist, authorizes the process capability, optionally authorizes a declared network capability, sanitizes the child environment, and launches with `shell=False` (`src/apos/core/execution.py:254-456`).

The service controls **which initial executable image is selected**, not what the executable can do. Arguments are unrestricted strings. There is no per-executable argument grammar, script-path boundary, module allowlist, child-process policy, OS API filter, or syscall boundary.

`cancel(request_id)` has no actor, capability check, or audit event of its own (`src/apos/core/execution.py:245-252`). Any code holding the service object and request ID can terminate an active process. `active_request_ids()` similarly has no authorization.

### Trusted executable capability expansion

The production composition root requires a caller-supplied `CommandPolicy`; the repository does not configure a production trusted-executable set. The only built-in factory is `CommandPolicy.current_python()`, and current uses outside tests are absent. Architecturally, however, any exact executable path can be supplied to `CommandPolicy`.

| Trusted executable class | Arbitrary code | Spawn subprocess/children | Escape project FS | Network | Environment control | Shell re-entry | OS API access |
|---|---:|---:|---:|---:|---:|---:|---:|
| Python interpreter | YES | YES | YES | YES | YES | YES | YES |
| Node.js interpreter | YES | YES | YES | YES | YES | YES | YES |
| PowerShell | YES | YES | YES | YES | YES | Native shell | YES |
| `cmd.exe` | YES | YES | YES | Via launched tools | YES | Native shell | Via launched tools |
| Git | Often | YES via helpers/hooks | YES | YES | YES | YES via helpers/hooks | Via helpers |
| `pip`/Python package manager | YES via build/install code | YES | YES | YES | YES | Possible | YES via package code |
| `npm`/`npx` | YES via lifecycle/package scripts | YES | YES | YES | YES | YES | YES via package code |
| Fixed-purpose native tool | Tool-dependent | Tool-dependent | Tool-dependent | Tool-dependent | Tool-dependent | Tool-dependent | Tool-dependent |

The table assumes adversarial arguments or adversarial project content. Exact-path trust is not a reduced capability profile. It is trust in the entire executable, its argument language, its configuration discovery, and all code it may load.

#### Python

Python accepts inline code, modules, scripts at arbitrary paths, startup/import behaviors, native extensions, `ctypes`, file APIs, sockets, subprocess APIs, registry APIs, and direct access to `COMSPEC`. The existing test `test_runs_without_shell_inside_project_and_audits_lifecycle` intentionally demonstrates inline Python writing a file, while `test_timeout_kills_child_process_tree` demonstrates Python spawning a child. These tests prove execution functionality, not confinement.

#### Node.js

Node can evaluate inline JavaScript, load arbitrary absolute scripts and native addons, access files and sockets, and spawn processes. Project-controlled module resolution and package scripts add further code-loading surfaces.

#### PowerShell and cmd.exe

Allowlisting either executable makes shell functionality directly available even though APOS itself passes `shell=False`. PowerShell can invoke .NET and native APIs, download content, access the registry, and launch processes. `cmd.exe /c` is explicit shell parsing. Filtering shell metacharacters only in `request.executable` does not constrain their interpretation in `args` by these programs.

#### Git

Git is both a version-control client and a process launcher. Depending on command and repository/user configuration, it can invoke hooks, credential helpers, SSH, remote helpers, pagers, editors, filters, diff drivers, merge drivers, submodule commands, and protocol helpers. Current `CommandPolicy` treats any trusted Git invocation as explicit network use, but network permission remains declarative and arguments are otherwise unrestricted.

#### Package managers

Package managers download attacker-controlled content and commonly execute build backends, lifecycle scripts, native compilation, and post-install hooks. Detecting the words `pip install` or selected npm operations does not cover all aliases, modules, configuration, direct API use, cache sources, or code-execution modes.

### `shell=False` security scope

`shell=False` provides these concrete protections for the `Popen` call:

- Python does not automatically place a shell between APOS and the selected executable.
- Shell metacharacters in an ordinary argument are passed as argument data rather than interpreted by an implicit shell.
- Common command concatenation and redirection payloads do not execute unless the selected program interprets them.
- The test `test_shell_metacharacters_in_argument_are_data` verifies this narrow property.

`shell=False` does **not** prevent:

- directly selecting `cmd.exe` or PowerShell as a trusted executable;
- an interpreter evaluating code supplied in arguments;
- a trusted process invoking a shell or any other child process;
- Git hooks, package scripts, compiler plugins, test plugins, or executable-specific command languages;
- filesystem, registry, network, COM, DLL, native-extension, or other OS API access;
- absolute paths or UNC paths supplied as interpreter arguments;
- child-process escape or audit-log tampering; or
- command-line parsing vulnerabilities inside the trusted executable itself.

### Environment handling

`EnvironmentSanitizer` constructs a reduced environment and rejects direct overrides of selected loader, path, and secret-looking names (`src/apos/core/execution.py:145-195`). This reduces accidental secret inheritance and simple `PATH`/`PYTHONPATH` injection.

It does not create an environment security boundary:

- arbitrary non-sensitive-looking variables remain allowed;
- language/tool-specific controls such as `NODE_OPTIONS`, Git configuration variables, SSH/helper settings, package-manager settings, plugin paths, and application-specific variables are not comprehensively modeled;
- `COMSPEC` and `PATHEXT` are blocked from request overrides but copied from the host into the child on Windows;
- `PATH` contains the trusted executable's directory, which may contain other launchable programs;
- an interpreter can ignore environment restrictions and use absolute paths or OS APIs; and
- host secrets remain accessible through files, agents, credential helpers, registry, DPAPI, IPC, and network services even when not inherited as environment variables.

### NetworkPolicy

The result explicitly reports `network_enforcement = "DECLARATIVE_ONLY"` (`src/apos/core/execution.py:409-423`). `CommandPolicy` detects only selected executable names, selected package-install argument patterns, and arguments beginning with HTTP URLs (`src/apos/core/execution.py:129-139`).

Consequences:

- Python socket/HTTP code, Node networking, PowerShell networking, native APIs, DNS, and custom clients can use the network while the request declares `NETWORK_DENIED`.
- A caller can declare `NETWORK_DENIED`, avoid the simple heuristic, and no `NETWORK_ACCESS` authorization occurs.
- `NETWORK_ALLOWED` and `NETWORK_APPROVAL_REQUIRED` both merely trigger the same separate capability request; the enum value itself does not force a particular decision.
- Approval of network access does not constrain destination, protocol, port, DNS, volume, duration, loopback, LAN, or internet scope.
- Network denial cannot protect secrets from exfiltration, prevent dependency download, or protect internal services.

Therefore any security argument that assumes `NETWORK_DENIED` means the child lacks network connectivity is invalid.

### AuditLog integrity

`AuditLog.record()` serializes one event, holds an in-process thread lock, opens the file in append mode, flushes, and calls `fsync()` (`src/apos/core/audit.py:130-174`). Its protection differs by threat:

| Threat | Protection | Assessment |
|---|---|---|
| Accidental mutation through normal `record()` use | Append mode, serialization, thread lock, flush/fsync | Good protection against accidental overwrite by that `AuditLog` instance. |
| APOS core file API mutation | `.apos` is denied by the default `SecretPolicy` | `FileSystemService` cannot normally edit the log, but this is not process-wide enforcement. |
| Other APOS/legacy Python code | None beyond convention | Direct `Path`, `open`, `unlink`, or `AuditLog.path` access can mutate it. |
| Multiple APOS processes/runtime instances | No shared lock or sequencer | Lines may race; there is no cross-process ordering or integrity guarantee. |
| Malicious subprocess under the same user | None | It can truncate, delete, rename, replace, forge, or block the log. |
| Offline tampering | None | No hash chain, signature, trusted timestamp, or remote copy detects rewriting. |

`fsync()` improves durability after a successful write. It does not provide authenticity, immutability, non-repudiation, or tamper evidence. The log location is inside the project and `APOS_PROJECT_ROOT` is explicitly provided to the child.

P0-3A's SQLite outbox is cross-process transactional for task state, but publication into JSONL is not cross-process serialized. `TaskService.flush_audit_events()` scans the full JSONL file for stable event IDs and `AuditLog.record()` repeats a scan while holding only its own instance lock. Separate processes can both observe an event as absent before either append completes, append duplicate event IDs, race ordering, or disagree about publication status. The current implementation has no OS file lock, named mutex, global sequencer, audit database transaction, or external queue. Same-process replay tests do not establish simultaneous multi-process publisher safety.

### ApprovalGrant authentication

P0-3A adds approval source semantics and a persistent task path. After the review fix, `ApprovalGrant` means human-originated approval intent only. It accepts only a `USER` actor and an unauthenticated local-user source. `SYSTEM`, `EXTERNAL_AI`, and `LOCAL_LLM` cannot construct a human grant under their actual actor kind. `AUTHENTICATED_HUMAN` and `authenticated=True` fail closed even for direct grant construction because no identity-proof provider exists. Deterministic system authorization remains a `PermissionDecision` produced by policy and is not recorded as human approval.

`TaskService` persists the canonical request digest, task, subject, approval source, expiry, and consumption state in SQLite. In `ProjectRuntime`, `PermissionEngine` delegates a persistent task approval to `TaskService`, which atomically consumes the grant and changes `APPROVED` to `RUNNING`.

This closes restart replay and concurrent double-consumption for the persistent task path. The digest binding is still useful **only if the grant issuer is authentic**. Today:

- any in-process caller or permissive adapter can construct `Actor(ActorKind.USER, "owner")`;
- there is no password, OS identity binding, signature, session proof, MFA, physical confirmation, or trusted approval channel;
- the supported `UNAUTHENTICATED_USER_REQUEST` source explicitly does not prove human identity;
- `AUTHENTICATED_HUMAN` is rejected because authentication is unimplemented;
- there is no issuer key, grant signature, audience, nonce authority, or revocation;
- legacy or standalone `PermissionEngine` instances without the persistent consumer retain the in-memory fallback; and
- caller-controlled request IDs can create ambiguity or denial-of-service conditions.

An external AI must never be allowed to submit a self-asserted `approved_by` actor or a raw grant treated as authoritative.

### ToolRegistry and adapter bypass

`ToolRegistry` stores definitions and schemas only. It does not bind a tool name to a handler, validate runtime input, derive actors, call `AuthorizationService`, or enforce that the declared capability matches the executed operation (`src/apos/core/tools.py:29-113`).

A future MCP/API/GUI adapter could therefore:

- treat registry metadata as authorization instead of calling the core service;
- dispatch directly to `subprocess`, `Path`, `GitClient`, `Kernel`, `evolution`, or Ollama code;
- trust client-supplied actor kind, capability, risk level, project root, or approval fields;
- add a handler whose actual behavior exceeds the registry capability;
- expose `cancel()` or audit paths without authorization;
- skip schema validation or validate against a schema different from the handler contract; or
- instantiate a separate permissive `PermissionEngine` or bypass `ProjectRuntime` entirely.

The registry cannot support a security invariant until one mandatory dispatcher owns handler binding, input validation, identity derivation, authorization, execution, and audit lifecycle.

### Privileged-operation invariant trace

Claim: **“Every privileged operation passes through `AuthorizationService`.”**

Current result: **False for the repository as a whole. Partially true for selected new core service methods.**

| Code path | AuthorizationService used? | Notes |
|---|---:|---|
| `FileSystemService.list_files/read_file/write_file` | YES | Boundary rejection and allowed operations are audited. TOCTOU remains. |
| `ControlledExecutionService.run` initial process | YES | Initial executable launch is authorized. Process behavior is not mediated afterward. |
| `TaskService.run_command_task` | YES, delegated | It invokes only `ControlledExecutionService`; persistent approval consumption claims `RUNNING` atomically. |
| Persistent task state/approval mutation | Task policy boundary | `ProjectRuntime` exposes `TaskService`, not its repository. Generic repository transitions reject `APPROVED` and `RUNNING`; dedicated transactions enforce grant issuance and consumption. |
| Declared non-denied network request | YES, decision only | No OS network enforcement; no started/finished network lifecycle. |
| `ControlledExecutionService.cancel` | NO | No actor or capability check. |
| `AuditLog.record/events` | NO | Directly callable infrastructure API. |
| `GitClient.run/commit/apply_patch` | NO | Direct `subprocess.run(["git", ...])`. |
| Legacy `executor.run_command` | NO | Direct `subprocess.run`, despite `shell=False`. |
| `CommandPatchCoder.run` | NO | Direct process execution. |
| Ollama HTTP/binary adapter | NO | Direct network and process execution. |
| `Kernel` file replacement and rollback | NO | Direct filesystem writes/deletes. |
| Evolution execution and Git workflows | NO | Direct process, filesystem, and Git operations. |
| Orchestrator proposal/state writes | NO | Direct filesystem and legacy service calls. |

`ProjectRuntime` now exposes `tasks` as the official task control plane and keeps the repository as a private-by-convention `TaskService` dependency. Repository types are not exported from the top-level `apos.core` API. This narrows accidental adapter bypass but is not a security boundary against malicious in-process Python, which can import infrastructure modules or inspect private attributes. No production CLI path instantiates the new runtime; legacy privileged paths remain.

### TOCTOU analysis

The main sequence is:

1. resolve a path or executable name;
2. evaluate and audit authorization; and
3. perform an operation later using the path string.

No stable file handle or Windows file identity is retained across those steps.

#### Workspace paths

- A checked directory can be renamed and replaced with a junction or other reparse point before `iterdir()`, `read_text()`, `mkdir()`, temporary-file creation, `os.replace()`, or `Popen(cwd=...)`.
- A checked file can be replaced after `is_file()` or `stat()` and before read.
- The read-size limit can be invalidated by replacing or growing the file after `stat()`.
- A non-existent write target has no file identity to bind; its parent chain can change after authorization.
- Authorization binds a normalized resource string and request metadata, not volume serial number, file ID, reparse tag, or opened handle.

#### Executables

- Trusted executables are resolved when `CommandPolicy` is constructed and again during assessment.
- The executable can be replaced, relinked, or updated after assessment and authorization but before Windows opens it in `CreateProcess`.
- No hash, publisher identity, file ID, open executable handle, ACL check, or immutable installation root is bound to the permission request.
- Dependencies loaded by the executable, including DLLs, modules, plugins, and configuration, are not assessed at all.

The current project lock does not close these races. It is a `threading.Lock` on one `ControlledExecutionService` instance and does not stop other APOS runtimes, other host processes, or the approved child itself.

## Windows-Specific Analysis

### Symlinks

`Path.resolve()` normally follows ordinary symlinks, so a stable pre-existing symlink escape is rejected. Creating symlinks may require Developer Mode or privilege on Windows, which can cause tests to use a junction fallback or skip. Symlink replacement after validation remains a TOCTOU risk.

### Junctions

Directory junctions are reparse points and can often be created without the same privilege required for symbolic links. Stable junctions to an outside directory are expected to resolve outside and be rejected; `test_rejects_outside_and_junction_working_directories` and `test_rejects_symlink_escape` cover this normal case. They do not test concurrent junction replacement between authorization and use.

### Reparse points

The implementation does not inspect reparse tags and does not open paths with `FILE_FLAG_OPEN_REPARSE_POINT`. It relies on `pathlib` resolution. Windows supports reparse-point types beyond ordinary symlinks and junctions, and behavior may vary by filesystem/provider. There is no handle-based `GetFinalPathNameByHandle` or file-ID validation immediately before operation.

### UNC paths

Direct UNC and extended absolute path strings supplied to `ProjectWorkspace.resolve()` are expected to be rejected as absolute after slash normalization. This does not prevent:

- registering the project root itself on a UNC share;
- passing a UNC path as an interpreter or shell argument;
- a process constructing a UNC path internally; or
- accessing SMB/network shares through native APIs.

### Executable resolution and PATHEXT

Bare executable names are resolved with `shutil.which()` using directories derived from the trusted set, then compared against the exact normalized trusted paths. This blocks straightforward project-local PATH hijacking at assessment time.

On Windows, name resolution still depends on platform executable-extension behavior and the host process state. `PATHEXT` is copied into the child. More importantly, exact executable selection does not constrain DLL search, plugins, imported modules, executable replacement races, or helper processes. A trusted executable directory writable by the APOS user is not an immutable trust root.

### COMSPEC

Request overrides of `COMSPEC` are rejected, but the host `COMSPEC` is copied into the child. A trusted interpreter can read it and invoke that shell. `shell=False` in the parent does not prevent child-initiated `cmd.exe` execution.

### Process tree

`CREATE_NEW_PROCESS_GROUP` creates a process group for signaling; it is not a Windows Job Object and does not impose containment. Timeout/cancellation calls `taskkill /PID ... /T /F` and then kills the direct process if needed. This is best-effort cleanup, not a guarantee that all descendants are tracked or terminated. Children may detach, use breakaway/job behavior, race termination, or persist outside the observed tree. `process_count_limit` is not enforced.

The internal `taskkill` invocation itself is launched by bare name using the APOS host environment, not a pinned System32 path.

### User privilege

The child inherits the APOS process token. If APOS runs as an administrator, the child receives that elevated context. If APOS runs as a normal developer, the child still receives access to that user's repositories, home directory, credentials, network identity, agents, and writable executable locations. There is no privilege drop or separate service account.

### File locking

- `AuditLog._lock` serializes threads using one `AuditLog` instance only.
- `ControlledExecutionService._project_lock` serializes one service instance only.
- Multiple runtimes and processes have independent locks.
- Stable task-event IDs and SQLite outbox state do not prevent two processes from racing the JSONL lookup/append boundary.
- No Windows range lock, lock file with verified ownership, named mutex, or transactional store coordinates audit or project operations.
- `os.replace()` can fail when Windows sharing modes deny replacement and does not prevent path substitution before the call.

## Current Guarantees

The following are real but narrowly scoped guarantees when callers use the P0-2/P0-3A core as designed:

- Missing static capability rules default to deny.
- Policy evaluation exceptions and wrong-capability responses fail closed.
- Approval matching covers project ID, request ID, actor, capability, resource, operation, risk, task ID, and metadata through the request digest.
- Standalone approval reuse is rejected within one `PermissionEngine` lifetime.
- Persistent task approval consumption and `APPROVED -> RUNNING` are one SQLite transaction.
- Persistent approval replay remains rejected after restart and under concurrent worker claims.
- Persisted `RUNNING` tasks become `RECOVERY_REQUIRED` at startup and are never automatically re-executed.
- Persistent task requests require their task approval even when the capability policy otherwise returns `ALLOW`.
- P0-3A rejects AI/SYSTEM human approval actions and keeps `AUTHENTICATED_HUMAN` unavailable.
- `ApprovalGrant` accepts only a `USER` approver; SYSTEM authorization is represented separately by `PermissionDecision`.
- `AUTHENTICATED_HUMAN` fails closed during direct grant construction as well as at the task boundary.
- `ProjectRuntime` exposes `TaskService`, not `TaskRepository`, as the official task interaction path.
- Generic repository transitions cannot enter `APPROVED` or `RUNNING`; those states require dedicated approval transactions.
- Core file APIs reject lexical absolute/traversal paths and stable resolved escapes.
- Default core file APIs deny configured secret names and `.apos`/`.git` paths.
- Command assessment requires an explicitly trusted resolved executable path.
- Project-relative executable selection and shell syntax in the executable field are rejected.
- The initial `Popen` call uses an argument vector and `shell=False`.
- Child stdin is closed, stdout/stderr are bounded in memory, and output is redacted on a best-effort basis.
- A timeout and cancellation path exist and attempt to terminate a process tree.
- Core filesystem and process operations emit correlated request/decision/lifecycle audit events.
- Audit writes flush and `fsync()` before returning.

## Current Non-Guarantees

The current implementation does not guarantee:

- OS-level confinement to the project directory;
- denial of filesystem access outside the project for an executed process;
- denial or destination scoping of network access;
- prevention of child-process or shell spawning;
- interpreter argument safety;
- DLL, module, plugin, hook, helper, or package-script safety;
- memory, CPU, disk, handle, or process-count limits;
- complete process-tree termination;
- immutable or tamper-evident audit records;
- authenticated human or system identity;
- authenticated approval issuance and replay protection outside the persistent P0-3A task path;
- a mandatory application-wide authorization gateway;
- cross-process project serialization;
- cross-process JSONL audit deduplication, ordering, and exactly-once publication;
- TOCTOU-resistant path or executable identity;
- protection of host credentials available outside environment variables; or
- safe remote exposure to an untrusted external AI.

## Known Exploitable Paths

The following paths are exploitable when their stated prerequisite is met:

1. **Trusted Python plus process authorization**: inline code or a script can directly use OS file, network, registry, and process APIs outside the project.
2. **Trusted Node.js plus process authorization**: evaluated or loaded JavaScript can perform equivalent host operations.
3. **Trusted PowerShell/cmd**: arguments become a direct shell command language despite parent `shell=False`.
4. **Network heuristic bypass**: an interpreter can create a socket without using a recognized tool name, URL argument, or package-install pattern while declaring `NETWORK_DENIED`.
5. **Child-process expansion**: a trusted process can launch any executable by absolute path; descendants do not receive separate APOS authorization.
6. **Audit destruction or forgery**: an authorized process can locate `.apos/audit/events.jsonl` under `APOS_PROJECT_ROOT` and mutate it with host-user rights.
7. **Approval forgery at an unsafe adapter**: a client that may submit actor/grant fields can label itself `USER`, calculate the visible request digest, and create an apparently valid grant.
8. **Legacy runtime bypass**: CLI/kernel/evolution/Git/Ollama paths perform process, file, Git, and network operations without the new authorization gateway.
9. **Git helper/hook execution**: permitted Git operations can trigger project or user configuration that launches external helpers.
10. **Package lifecycle execution**: permitted package-manager operations can execute downloaded or project-controlled build/lifecycle code.
11. **Path race**: another same-user process can replace a checked directory/target with a reparse point between resolution/authorization and use.
12. **Executable race or dependency hijack**: a trusted executable or load dependency in a user-writable location can change after assessment.

## OS Boundary Requirements

A true Windows project sandbox requires an OS-enforced broker/worker design. Minimum requirements are:

- execute untrusted work in a separate low-privilege identity or isolated VM/container/AppContainer appropriate to the threat model;
- apply a restricted token and remove unnecessary privileges;
- use a Windows Job Object for kill-on-close, child tracking, process-count, memory, CPU, and breakaway restrictions;
- expose only the intended project tree through ACLs, a copied workspace, virtual disk, container mount, or brokered file handles;
- keep credentials, audit storage, control sockets, and APOS source outside the worker's writable namespace;
- enforce network denial/allow rules outside the child using Windows Firewall/WFP, container networking, or VM policy;
- pin executable identity using protected installation roots plus hash/publisher/file-ID verification;
- control DLL/module/plugin/helper search paths and disable unsafe project/user configuration;
- validate final paths by opened handles and reject unexpected reparse points;
- use a separate, controlled temporary directory within the sandbox;
- enforce disk and output quotas; and
- terminate the Job Object or isolated worker, not only the apparent parent PID.

## Remote Exposure Requirements

Before any network exposure, APOS requires:

- authenticated transport with TLS and server authentication;
- authenticated client identity mapped server-side to a fixed actor and project scope;
- authorization claims that clients cannot override in request bodies;
- strict request size, rate, concurrency, timeout, and output limits;
- a mandatory dispatcher that exposes an allowlisted method set only;
- no direct object, filesystem path, subprocess, Git, or legacy CLI access;
- destination-enforced network policy for worker processes;
- isolation between projects and tenants;
- approval through an independent trusted channel, not the requesting AI session;
- durable nonce/replay protection and session-bound request correlation;
- tamper-evident audit export to a location the worker cannot modify;
- secret-management integration that returns scoped handles or tokens rather than raw secrets; and
- incident controls including revocation, kill switch, retention, and alerting.

Until these controls exist, remote access must be treated as remote code execution under the APOS host user's account whenever process execution can be reached.

## Approval Authentication Requirements

A production approval system must:

- authenticate the human using an OS-backed or cryptographic identity;
- issue grants only in a trusted UI/service outside the untrusted AI channel;
- sign or MAC a canonical grant containing project, request digest, capability, resource, actor, audience, issuance time, expiry, and nonce;
- display the exact command/script/content digest, cwd, environment keys, network scope, and risk to the approver;
- prohibit client-selected `USER` and `SYSTEM` identities;
- define narrowly when `SYSTEM` may approve and protect its signing key;
- persist consumption and revocation atomically across restarts and processes;
- reject duplicate request IDs and stale project state;
- bind approval to immutable resource identity where possible; and
- audit both approval issuance and consumption in a tamper-evident store.

## Audit Integrity Requirements

For audit records to serve as security evidence, APOS requires:

- storage outside the project worker's writable boundary;
- append authorization controlled by a separate broker or service identity;
- per-event sequence numbers and a hash chain or Merkle structure;
- signatures/MACs anchored in a key inaccessible to the worker;
- trusted timestamps or a remote log collector;
- atomic cross-process serialization;
- detection of truncation, deletion, reordering, insertion, and forked histories;
- authenticated actor and approval-event linkage;
- explicit log-write failure behavior that fails privileged operations closed where appropriate;
- secure rotation, retention, backup, and verification tooling; and
- redaction tests for structured fields, arguments, output, paths, and newly introduced adapters.

## MCP Adapter Security Requirements

No MCP adapter should be implemented or exposed until a mandatory dispatcher exists. That dispatcher must:

- map each public tool name to exactly one reviewed handler;
- derive capability, risk, operation, project, and actor server-side;
- reject client attempts to set actor kind, capability, risk, project root, executable trust, or approval authority;
- validate inputs against a closed schema and then construct typed requests;
- call only `ProjectRuntime` capability services for privileged operations;
- prohibit imports or calls into legacy `Kernel`, `GitClient`, executor, evolution, Ollama, and direct filesystem/process helpers;
- treat `ToolRegistry` as descriptive metadata only, never as proof of authorization;
- require authenticated, independently issued approval artifacts;
- apply authorization to cancellation, status, audit reads, and every newly added tool;
- keep one authoritative runtime/policy store per project or coordinate them durably;
- record request, decision, start, finish, response truncation, and adapter identity; and
- include negative conformance tests proving there is no alternate dispatch path.

## P0-3 Security Preconditions

P0-3 should not widen the trust boundary until all of the following gates are met:

1. Declare current P0-2 execution as **local cooperative mode**, not sandbox mode.
2. Quarantine or migrate every legacy privileged path behind one authorization dispatcher.
3. Define an authenticated identity and approval protocol with durable replay protection.
4. Choose and prototype a Windows OS isolation strategy for interpreter execution.
5. Enforce network policy outside the child process.
6. Move audit authority outside the project worker and add tamper evidence.
7. Replace pathname-only sensitive operations with handle/file-identity validation where feasible.
8. Define executable-specific profiles; do not treat a generic interpreter as a narrow tool.
9. Enforce process-tree, memory, process-count, CPU, disk, and time limits through the OS.
10. Add security conformance tests for adapter bypass, process escape, network denial, audit tampering, approval forgery, and TOCTOU.

These are preconditions for expanding exposure, not an instruction to implement P0-3 in this review.

## Recommended Mitigations

### Immediate

- Label `ControlledExecutionService` and `NetworkPolicy` accurately as logical controls, not a sandbox.
- Do not expose process execution to an untrusted remote actor.
- Do not allow Python, Node.js, PowerShell, cmd, Git write operations, or package managers for untrusted requests without OS isolation.
- Keep GitHub/API/MCP/GUI adapters disconnected from privileged core services until identity and dispatch requirements are met.
- Treat audit data as operational telemetry, not immutable security evidence.

### Architecture

- Introduce a single mandatory command dispatcher and eliminate alternate privileged entry points.
- Separate a trusted broker from an untrusted worker process.
- Use capability-specific operations instead of generic interpreter command execution.
- Bind approvals to authenticated identities and persistent state.
- Move audit writes to the broker or a remote append-only service.
- Use Windows Job Objects and a restricted execution identity or stronger isolation.
- Enforce filesystem and network access externally to the worker.

### Path and executable hardening

- Open and validate path handles immediately before use; inspect reparse tags and final paths.
- Bind authorization to file identity and relevant content digest when practical.
- Protect trusted executables in administrator-controlled, non-user-writable locations.
- Verify executable hash/publisher and revalidate at launch.
- Pin internal system tools such as `taskkill.exe` to trusted absolute paths.
- Define per-executable argument schemas and deny interpreter evaluation modes in non-sandboxed operation.
- Disable or isolate Git hooks, helpers, pagers, editors, filters, and user/global configuration.

## Security Test Evidence

The following current tests support limited security assumptions:

| Test | What it verifies | What it does not verify |
|---|---|---|
| `tests/test_core_filesystem.py::ProjectFileSystemTests::test_rejects_absolute_and_traversal_paths` | Core file API rejects direct absolute and traversal strings. | Process access, races, exotic reparse points. |
| `...::test_denies_secret_paths_by_default` | Default name-based secret denial. | Content-based secrets or direct process access. |
| `...::test_rejects_symlink_escape` | Stable symlink/junction escape is rejected when link creation is available. | Concurrent link swap; all Windows reparse tags. |
| `...::test_denies_link_alias_to_internal_secret_directory` | Stable alias to `.apos` is denied. | Malicious subprocess mutation. |
| `tests/test_core_execution.py::ControlledExecutionTests::test_runs_without_shell_inside_project_and_audits_lifecycle` | Launch/audit lifecycle works with `shell=False`. | OS confinement; it explicitly executes arbitrary Python. |
| `...::test_shell_metacharacters_in_argument_are_data` | No implicit parent shell interprets an ordinary argument. | Interpreter/shell argument languages and child shells. |
| `...::test_rejects_shell_syntax_and_project_path_hijacking` | Executable-field metacharacters and project-relative executable are denied. | Executable replacement, DLL/module/plugin hijack. |
| `...::test_rejects_outside_and_junction_working_directories` | Stable outside cwd and directory link are rejected. | TOCTOU after cwd validation. |
| `...::test_timeout_kills_child_process_tree` | One tested child tree is killed on the test platform. | Detached/breakaway children and adversarial races. |
| `...::test_external_cancellation_kills_running_process` | Cancellation terminates one direct run. | Authorization of cancellation and complete tree containment. |
| `...::test_bounds_large_output` | Returned stdout is bounded. | Memory/CPU/disk/process quotas. |
| `...::test_sanitizes_environment_and_redacts_secret_output_and_audit` | Selected secret environment inheritance and known-value output are redacted. | Other credential stores and unknown secret forms. |
| `...::test_rejects_environment_injection_and_explicit_network_command` | `PYTHONPATH` override and recognized pip install are denied. | Direct socket/network APIs and heuristic bypasses. |
| `...::test_network_access_has_separate_approval_request` | Separate network decision request is generated. | Network isolation; the test confirms `DECLARATIVE_ONLY`. |
| `...::test_permission_approval_is_bound_to_exact_request` | Approval matches exact execution request digest. | Human authenticity. |
| `...::test_permission_approval_cannot_be_reused_with_changed_environment` | Changed environment invalidates a grant. | Restart replay and forged approver identity. |
| `tests/test_core_security.py::PermissionEngineTests::test_allows_explicit_safe_capability_and_denies_missing_rule` | Missing policy rule fails closed. | Actor/resource-aware policy. |
| `...::test_risky_action_requires_matching_trusted_approval` | Exact grant and in-memory one-time consumption. | Trusted issuance and durable replay defense. |
| `...::test_external_ai_cannot_issue_approval` | Dataclass rejects an `EXTERNAL_AI` approver label. | A caller forging a `USER` label. |
| `...::test_system_actor_cannot_issue_human_approval` | `SYSTEM` cannot construct a human `ApprovalGrant`. | Authentication of a caller that self-labels as `USER`. |
| `...::test_authenticated_human_grant_fails_closed_without_identity_proof` | Direct authenticated-human grant construction fails closed. | Real human authentication. |
| `...::test_system_authorization_is_a_policy_decision_not_an_approval_grant` | Policy `ALLOW` remains a `PermissionDecision`, separate from human approval. | Policy-store authenticity and actor-aware policy. |
| `...::test_policy_error_fails_closed` | Policy exception becomes deny. | Availability and authenticated policy storage. |
| `...::test_allowed_operation_records_full_lifecycle_and_correlation` | Expected audit event sequence and correlation. | Log authenticity and immutability. |
| `...::test_audit_redacts_sensitive_keys_inline_tokens_and_known_values` | Selected redaction patterns. | Complete data-loss prevention. |
| `tests/test_core_runtime.py::ProjectRuntimeTests::test_composes_one_project_scoped_runtime_and_tool_registry` | Core services share one project; runtime exposes `tasks` without a public repository field or top-level repository export. | Malicious in-process introspection or mandatory use by every adapter. |
| `tests/test_core_tasks.py::PersistentTaskTests::test_consumed_approval_cannot_be_reused_after_restart` | Persistent approval consumption survives restart. | Human identity authenticity or DB tamper resistance. |
| `...::test_repository_transition_cannot_replace_approval_transactions` | Generic persistence transitions cannot enter `APPROVED` or `RUNNING`. | Malicious direct SQL or same-user database tampering. |
| `...::test_two_workers_cannot_consume_one_approval_or_start_one_task_twice` | SQLite serializes two concurrent task claims. | Multi-host databases or OS process containment. |
| `...::test_running_task_recovers_without_automatic_execution` | Startup changes `RUNNING` to `RECOVERY_REQUIRED` without replay. | Whether an old detached OS process still survives. |
| `...::test_authenticated_human_source_is_explicitly_unimplemented` | The default boundary rejects authenticated-human claims. | Real human authentication. |
| `...::test_runtime_execution_consumes_persistent_approval_before_process_start` | Direct task execution without approval is denied; the official service path consumes persistent approval before controlled execution. | OS sandboxing, cross-process audit publication, or trusted-interpreter confinement. |

Tests may skip link coverage when Windows cannot create a symlink or junction. None of the current tests establishes an adversarial OS sandbox.

## Residual Risks

Even after logical improvements, residual risk remains whenever arbitrary code executes:

- OS isolation mechanisms can have kernel, container, filesystem, or policy escape vulnerabilities.
- Build tools and compilers have large trusted computing bases.
- Network allowlisting can be bypassed through allowed proxies or confused-deputy services.
- Redaction can miss unknown secret formats or leak information through behavior and timing.
- Human approvers can be deceived by complex or misleading requests.
- Signed audit logs prove recorded history, not that every privileged action was recorded.
- Git and dependency ecosystems introduce mutable upstream and supply-chain risk.
- Windows path semantics and third-party reparse providers require platform-specific regression testing.

The security posture must therefore be expressed as a set of explicit modes and assumptions, not as a blanket statement that APOS is sandboxed.

## Final Security Decisions

### Can APOS currently be safely exposed to an untrusted external AI over a network?

**NO.**

P0-3A provides durable task and approval state but no authenticated remote identity or human approval proof. There is still no mandatory dispatcher, OS process sandbox, enforced network isolation, or tamper-resistant audit boundary, and multiple legacy privileged paths bypass `AuthorizationService`. If process execution is reachable, a trusted interpreter can become remote code execution with the APOS host user's rights.

### Can APOS currently provide a true OS-level project sandbox?

**NO.**

`ProjectWorkspace` provides logical path validation for selected APIs. Executed processes retain the host user's OS rights and are not confined by a restricted token, filesystem namespace, Job Object security policy, container, VM, or firewall boundary.

### Can a trusted interpreter potentially escape the APOS logical project boundary?

**YES.**

A trusted interpreter can use its own filesystem, network, subprocess, shell, registry, native-extension, and OS APIs. `shell=False`, cwd validation, environment reduction, and executable allowlisting do not confine interpreter behavior after launch.
