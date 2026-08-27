# P0-3B Architecture Decision: Execution Isolation

Status: Proposed architecture, documentation only

Baseline: `dd55f31e1acfd1101adb732cacd3290ad021b273`

Decision scope: the execution boundary after P0-3A

## Executive decision

P0-3B should not be implemented as one universal Windows sandbox. It should add a mandatory execution broker and a small provider interface, then deliver two useful execution profiles in order:

1. A clearly labeled cooperative host profile, strengthened with Windows Job Objects for process-tree and resource control.
2. A staged, containerized profile for untrusted or generated Linux-compatible code, with no network by default and no writable mount of the source repository.

The broker, manifest, staging, artifact return, and audit protocol are the architectural boundary. Job Objects, restricted workers, containers, and VMs are replaceable isolation providers. No adapter or local model may call an isolation provider directly.

The current `ControlledExecutionService` is not an OS sandbox. `shell=False`, command policy, environment sanitization, timeout, cancellation, and output limits are valuable controls, but an allowed interpreter can still use the caller's OS token, filesystem access, network stack, and process APIs.

## Current state

### Implemented foundation

- P0-1: `ProjectWorkspace`, secret-path policy, and controlled project-relative filesystem operations.
- P0-2: `PermissionEngine`, `AuthorizationService`, append-oriented audit records, and `ControlledExecutionService`.
- P0-3A: persistent tasks and approvals, lifecycle state machine, atomic claim, one-time approval consumption, restart recovery, and `TaskService` as the official task control plane.
- `ProjectRuntime` is the composition root for the modern core.
- `ToolRegistry` is metadata only. It is not a dispatcher or security boundary.

### Important architectural gap

The production CLI/CUI, `Kernel`, `executor`, `coder`, `GitClient`, orchestrator, and evolution paths still contain prototype-era direct filesystem and subprocess behavior. They do not all enter through `ProjectRuntime`, `TaskService`, and `AuthorizationService`. P0-3B must not make the mistake of placing a sandbox beside those paths while leaving bypasses intact.

### Dependency graph

```text
P0-1 workspace policy
    -> P0-2 authorization + audit + controlled execution
        -> P0-3A persistent task/approval control plane
            -> P0-3B execution broker + isolation providers
                -> authenticated adapters and remote control
```

An isolation provider must consume an already authorized, claimed task. It must not create approvals, alter task authority, or infer policy from transport metadata.

## Problem statement

APOS needs to execute tests, build commands, and generated code on a Windows development machine without turning a remote AI request into unrestricted execution under the interactive user's identity. The design must account for four distinct concerns:

- Policy: whether an operation is allowed.
- Authority: who may request, approve, execute, observe, or recover it.
- Containment: what the resulting OS process can actually reach.
- Recovery: how APOS terminates, records, and reconciles incomplete work.

Authorization cannot substitute for containment. Containment cannot substitute for authenticated authority. Git checkpoints cannot substitute for either one.

## Required execution contract

Every executable task must become an immutable `ExecutionManifest` before launch. The exact schema is future implementation work, but it must bind at least:

- task ID, attempt ID, project ID, actor and operator IDs;
- executable identity and argv, never a shell command string;
- working-directory identity;
- source snapshot hash and staged-workspace identity;
- requested isolation profile and effective provider;
- filesystem, network, process, environment, and resource capabilities;
- timeout, output limits, artifact rules, and expected result type;
- authorization record and consumed approval IDs;
- manifest hash, creation time, nonce, and expiry.

The broker must reject any provider result whose task, attempt, manifest hash, or workspace identity does not match. Provider output is untrusted data and must be size-limited, redacted, and recorded before presentation to an AI or user.

## Isolation technology comparison

| Technology | Integration | Filesystem and network boundary | Process/resource control | Compatibility and operations | Security conclusion |
|---|---|---|---|---|---|
| Current Python subprocess | Already present | Same user token and host namespace; project restriction is cooperative; network policy is declarative | Timeout/cancel/output caps; child cleanup is best effort | Highest Windows tool and local LLM compatibility | Useful execution wrapper, not a sandbox |
| Windows Job Objects | Moderate via Win32 bindings | No filesystem or network isolation | Strong process-tree accounting, kill-on-close, active-process, CPU, memory, and time limits; breakaway must be denied | Native tools work; low admin burden; crash cleanup is practical | Required hardening for host execution, not sufficient containment |
| Restricted token | High | Removes privileges/SIDs but access still follows Windows ACLs; no filesystem or network namespace | Combine with Job Object; child processes normally inherit restricted context | Native compatibility varies; worker account/profile and ACL setup are operationally heavy | Useful defense in depth for a staged worker, not a stand-alone sandbox |
| AppContainer | Very high | Strong capability/ACL model and network capabilities can be withheld | Combine with Job Object for quotas and process tree | Win32 dependency, profile, TEMP, package/capability, and ACL integration are complex; many developer tools assume broader access | Promising narrow provider prototype, not the first universal provider |
| Windows Sandbox | High | Disposable Windows environment; networking is enabled by default and must be disabled; mapped folders can expose host files | VM lifecycle gives coarse termination; fine quotas and structured result extraction need orchestration | Strong Windows compatibility, but optional Windows feature, virtualization, startup latency, and automation friction | Good strong profile for occasional Windows-specific jobs, not the default |
| WSL 2 alone | Moderate | Linux filesystem in a utility VM, but Windows drives and interop deliberately bridge to the host | Linux controls possible; WSL distributions share a kernel/VM and lifecycle | Excellent Linux developer UX; host interop is a feature; cross-filesystem access can be slower | Execution substrate, not a security boundary by itself |
| Docker Desktop/Linux container | Moderate | Explicit mounts; bind mounts are writable by default; `--network none` is available | CPU/memory/PID/time limits must be explicitly configured; cleanup is mature | Good test/build compatibility for Linux-capable projects; Windows-native and GUI workloads do not fit; Docker installation/backend required | Best practical staged profile when configured without host-write mounts or daemon socket |
| Hyper-V VM | Very high | Dedicated kernel and virtual hardware; narrow copy-in/copy-out can provide the strongest boundary | Strong lifecycle and resource controls | Heavy image lifecycle, startup/storage cost, admin/edition/virtualization requirements; local GPU/model access is difficult | Strongest Windows option for hostile workloads, reserved for high-risk profiles |
| Separate low-privilege worker | Moderate to high | Depends on account ACLs and staging; network remains unless separately controlled | Job Object and OS account improve containment and cleanup | Native tools can work, but tool installation, profile, credentials, and cleanup need management | Necessary architectural separation; strength depends on the provider beneath it |

### Control-detail matrix

| Technology | Project access semantics | Network control | Child tree | CPU | Memory | Timeout/crash cleanup |
|---|---|---|---|---|---|---|
| Current subprocess | Host path as current user | None; declaration only | Best-effort Windows group/termination | No hard quota | No hard quota | Timeout exists; descendants may be difficult outside a job |
| Job Objects | Unchanged host access | None | Strong if assigned before resume and breakaway is denied | Rate/time limits | Process/job limits | Kill-on-close and completion events |
| Restricted token | Staging ACLs can narrow access; no namespace | No general deny by itself | Inherited token plus Job Object | Via Job Object | Via Job Object | Worker/job lifecycle required |
| AppContainer | Explicit capability and ACL grants | Capabilities can withhold network | AppContainer plus Job Object | Via Job Object | Via Job Object | Profile/temp/resource cleanup is APOS work |
| Windows Sandbox | Copy or mapped folder; writable mappings expose host | Configurable, but enabled by default | Confined to disposable VM | VM-level/coarse | Configurable VM memory | Tear down whole environment; collect results first |
| WSL 2 alone | Linux VHD plus intentional `/mnt/c`/interop paths | Shared/bridged WSL networking, not an APOS deny | Linux process controls possible | WSL/container configuration | WSL/container configuration | Distribution/VM lifecycle is broader than one task |
| Docker Desktop | Internal volume preferred; bind mounts require `ro` and remain host-coupled | `--network none` or explicit network | PID limit/container lifecycle | Explicit quota | Explicit quota | Mature stop/remove; APOS must verify cleanup |
| Hyper-V VM | Virtual disk and copy-in/out | Virtual switch/firewall or no NIC | VM boundary | VM quota | VM quota | Hard VM teardown; image/disk cleanup required |
| Low-priv worker | ACL-isolated staging if configured correctly | None without another mechanism | Job Object recommended | Job Object | Job Object | Supervisor, lease, profile/temp cleanup required |

### Product and operational matrix

| Technology | Native Windows/GUI | Tests and toolchains | Local LLM/GPU | Admin/install burden | Portability and usability |
|---|---|---|---|---|---|
| Current subprocess | Best compatibility, including GUI | Best compatibility | Direct local access | None beyond tool install | Simple but unsafe for untrusted work |
| Job Objects | Native CLI; GUI technically possible but inappropriate for unattended tasks | Broad compatibility; tools needing their own job must be tested | Direct local access | Low; Win32 integration | Windows-only provider detail, low user friction |
| Restricted token | Native tools may fail on profile/ACL assumptions; GUI desktop isolation is separate | Tool installs and temp/profile behavior need curation | Model service should remain outside worker and accessed only by a broker | Medium/high account and ACL management | Windows-specific, operationally fragile |
| AppContainer | GUI/package scenarios possible, generic developer tools often incompatible | Per-tool capability engineering | GPU/model connectivity is capability-dependent and risky | High profile/SID/ACL/API work | Windows-specific, poor universal developer UX |
| Windows Sandbox | Strong Windows and some GUI compatibility | Good after image/bootstrap cost | vGPU exists but should be off by default; host model access conflicts with isolation | Optional feature, virtualization, supported edition/admin setup | Windows-only, noticeable startup and transfer friction |
| WSL 2 alone | Linux CLI, not native Windows GUI semantics | Excellent Linux toolchains | GPU support varies; host interop weakens boundary | WSL install and distro management | Good Windows developer UX, Windows-only substrate |
| Docker Desktop | Linux CLI; no native Windows GUI | Excellent reproducible Linux tests/builds | GPU mainly through WSL2 backend and weakens device boundary | Docker/backend install, image lifecycle | Container contract is portable; desktop implementation varies |
| Hyper-V VM | Full guest Windows/GUI possible | Broad if image is maintained | GPU partition/passthrough is complex and outside MVP | Highest edition/admin/image/storage burden | Strong but heavy and Windows-host specific |
| Low-priv worker | Native CLI, possible GUI but avoid interactive desktop | Good only for preinstalled curated tools | Brokered model access preferred | Account/profile/tool patching required | Concept portable; implementation OS-specific |

## Windows-specific implications

### Job Objects

The host provider should create the process suspended, assign it to a non-breakaway Job Object, configure limits, and only then resume it. Required controls are `KILL_ON_JOB_CLOSE`, active-process limit, per-process and per-job memory limits, CPU rate or time limit, and a completion port for lifecycle events. Job Objects govern a process tree but do not prevent a process from reading user files, opening sockets, using COM, or invoking OS APIs.

### Restricted tokens and worker accounts

A restricted token can disable SIDs and privileges and add restricting SIDs. It does not create a namespace. A worker should therefore receive only an ACL-isolated staging directory, a private temporary directory, and an intentionally constructed environment. The original repository, SSH directory, browser profiles, cloud credentials, and APOS state database must not be readable. A dedicated low-privilege account is easier to reason about than repeatedly deriving tokens from the interactive account, but it creates tool-installation and profile-management work.

### AppContainer

AppContainer can withhold network capabilities and require explicit filesystem grants. Its integration requires profile creation, SID and ACL management, `STARTUPINFOEX` attributes, compatible temporary paths, and testing every toolchain. It is attractive for a narrow native worker but risky as P0-3B's first delivery because compatibility failures could force broad capabilities that erase the intended benefit.

### Windows Sandbox and Hyper-V

Windows Sandbox must be configured with networking, clipboard, vGPU, and mapped folders disabled unless a task explicitly requires them. Writable mapped host folders are incompatible with a strong isolation claim. APOS would need a copy-in image or read-only source plus a separate, validated copy-out channel. Hyper-V offers a stronger and more controllable boundary, but image patching, boot latency, disk growth, and result transport make it a later provider.

### WSL and Docker Desktop

WSL intentionally exposes Windows drives and Windows/Linux interoperability. It cannot be labeled a sandbox merely because the process is Linux. Docker Desktop can provide a much narrower container contract, but only if APOS forbids the Docker socket, privileged containers, host PID/network modes, device passthrough, and writable project bind mounts. A Docker provider should prefer copying a source snapshot into an internal volume and exporting a patch/artifact bundle afterward.

## APOS execution profiles

These profiles are product policy, not marketing labels. Each must expose its actual guarantees in task and audit results.

### Profile E0: inspect only

- Use: task planning, metadata, status, and explicitly authorized reads.
- Tools: no executable tools.
- Filesystem: read operations through `FileSystemService`; secrets denied.
- Network/process: none.
- Approval: normal read policy; no execution approval.
- Audit/recovery: every privileged read audited; no process recovery.

### Profile E1: trusted host tool

- Use: deterministic, user-selected native tools whose arguments APOS constructs, such as a known test runner in a trusted repository.
- Tools: executable allowlist by canonical resolved path and identity; interpreters only with explicit high-risk classification.
- Filesystem: host user access remains technically possible; APOS path policy is cooperative.
- Network: not isolated. A declared deny is not an enforced deny.
- Process: Job Object, no breakaway, explicit child limit, timeout, memory/CPU/output caps.
- Approval: human approval for interpreters, package managers, launchers, writes, and any network-capable task.
- Audit/recovery: manifest, executable identity, effective limits, result, and incomplete process-tree cleanup recorded; restart becomes `RECOVERY_REQUIRED`.

This profile must be labeled `HOST_COOPERATIVE`, never `SANDBOXED`.

### Profile E2: staged restricted native worker

- Use: Windows-native tests/builds that cannot run in a Linux container and do not justify a VM.
- Tools: installed allowlist inside a dedicated low-privilege worker context.
- Filesystem: copied snapshot in an ACL-isolated staging root; original repository and APOS state unavailable.
- Network: denied only when an enforceable AppContainer or firewall-backed worker policy exists; otherwise report `NOT_ISOLATED`.
- Process: restricted account/token plus Job Object and quotas.
- Approval: human approval for first use, capability expansion, dependency installation, and network.
- Audit/recovery: staged tree hash, ACL/profile identity, copy-out manifest, and cleanup result.

This profile is `RESTRICTED`, not a robust hostile-code boundary unless its network and resource controls are proven.

### Profile E3: staged container

- Use: generated code, dependency installation, tests, and builds compatible with Linux containers.
- Tools: image-pinned toolchain; no host Docker socket or privileged flags.
- Filesystem: content-addressed copy-in; internal writable layer; result returned as patch plus declared artifacts. No writable bind mount of the source repository.
- Network: none by default; allowlisted egress is a separate future capability, not a boolean switch.
- Process: non-root user, PID/CPU/memory/time/output limits, read-only base filesystem where practical.
- Approval: approval for execution risk; separate approval for image acquisition, dependency network, or artifact application.
- Audit/recovery: image digest, manifest hash, limits, network mode, output/artifact hashes, and cleanup status.

This is the recommended default for untrusted Linux-compatible execution.

### Profile E4: disposable VM

- Use: high-risk or Windows-specific generated workloads requiring a true kernel boundary.
- Tools: prebuilt Windows Sandbox or Hyper-V image; no host credentials.
- Filesystem: copy-in/copy-out only; no writable host mapping.
- Network: disabled by default; temporary policy-specific egress through an observable proxy if later required.
- Process: VM quotas and hard teardown.
- Approval: authenticated human approval for every launch and every capability expansion.
- Audit/recovery: image version, VM configuration, transfer hashes, teardown attestation, and residual disk handling.

This profile is optional after the MVP because installation and operating costs are high.

## Provider architecture

```text
Adapter / local UI / model
          |
          v
TaskService -> AuthorizationService -> ExecutionBroker
                                      | validate immutable manifest
                                      | stage snapshot
                                      v
                         IsolationProvider interface
                  +-----------+-----------+-----------+
                  | Host/Job  | Container | VM/native |
                  +-----------+-----------+-----------+
                                      |
                                      v
                         Result + artifact verifier
                                      |
                     audit -> task state -> presentation
```

Provider operations should be limited to `prepare`, `launch`, `observe`, `cancel`, `collect`, and `destroy`. Providers do not mutate the source project. A separate authorized artifact-application operation applies a reviewed patch after execution.

## Rejected approaches

### Keep the current subprocess service and call it a sandbox

Rejected because an allowed Python, Node.js, PowerShell, `cmd.exe`, Git hook, package manager, or compiler plugin can invoke arbitrary OS APIs and child processes under the user's token. `shell=False` prevents implicit command-shell parsing; it does not constrain the executable.

### Job Objects as the complete P0-3B boundary

Rejected as a complete boundary because they do not isolate files, credentials, registry, COM, or network. Adopted only as mandatory host-process hardening.

### Restricted token alone

Rejected because access remains governed by host ACLs and network is not namespaced. It is useful only with a dedicated staging root, account/profile design, process-tree control, and an enforceable network mechanism.

### AppContainer as the universal first provider

Deferred because developer toolchains commonly need complex DLL, filesystem, registry, subprocess, and temporary-directory behavior. Broadly granting capabilities to make tools run would produce a fragile boundary with high integration cost.

### WSL as the sandbox

Rejected because host-drive mounts and Windows/Linux interop are core features. WSL may host Docker or a worker, but WSL alone does not meet the APOS containment contract.

### Windows Sandbox as the default

Deferred because it is an optional feature with virtualization requirements, noticeable startup, coarse automation, and awkward structured copy-out. It remains a useful later Windows provider.

### Direct writable repository mounts into containers or VMs

Rejected because a compromised process can mutate the host project concurrently, evade artifact review, race Git, and damage untracked files. Copy-in/copy-out makes mutation explicit and auditable.

## Implementation sequence

1. Close execution bypasses: define the single broker entry point and forbid production adapters from direct `subprocess`, direct filesystem mutation, or direct provider access.
2. Define immutable execution and result manifests with stable serialization and hashes.
3. Add staging, artifact verification, and an explicit apply operation separate from execution.
4. Implement E1 with Job Object process-tree/resource enforcement and truthful guarantee reporting.
5. Add broker/provider contract tests, crash recovery tests, executable identity tests, and adversarial interpreter tests.
6. Implement E3 container provider behind the same contract, with pinned images and deny-by-default capabilities.
7. Prototype E2 only for real Windows-native compatibility gaps.
8. Add E4 only when user workloads demonstrate the need.

## P0-3B completion criteria

- Every production execution path enters `TaskService`, authorization, and one `ExecutionBroker`.
- No adapter can invoke a provider or subprocess directly.
- An execution manifest is immutable, hashed, task-bound, attempt-bound, authorized, and audited.
- The source repository is never directly writable by E3/E4 workloads.
- E1 terminates and accounts for the complete process tree under a Job Object.
- Every result reports effective, not merely requested, filesystem/network/process/resource guarantees.
- Recovery reconciles claimed tasks, provider state, artifacts, and cleanup after APOS or worker failure.
- Security tests demonstrate interpreter escape from E1 remains possible and is honestly classified.
- At least one staged provider prevents a malicious test from reading host secrets, mutating the source tree, using the network, or leaving child processes.

## Research basis

- Microsoft, [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects), [Basic limit information](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information), and [Nested jobs](https://learn.microsoft.com/en-us/windows/win32/procthread/nested-jobs).
- Microsoft, [CreateRestrictedToken](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-createrestrictedtoken) and [Windows security model](https://learn.microsoft.com/en-us/windows-hardware/drivers/driversecurity/windows-security-model).
- Microsoft, [AppContainer isolation](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation) and [Implementing an AppContainer](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer).
- Microsoft, [Windows Sandbox architecture](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-architecture), [configuration](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file), and [installation requirements](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-install).
- Microsoft, [Windows container isolation](https://learn.microsoft.com/en-us/virtualization/windowscontainers/manage-containers/hyperv-container) and [container security](https://learn.microsoft.com/en-us/virtualization/windowscontainers/manage-containers/container-security).
- Microsoft, [WSL overview](https://learn.microsoft.com/en-us/windows/wsl/about), [filesystem guidance](https://learn.microsoft.com/en-us/windows/wsl/filesystems), and [Windows/Linux interoperability](https://learn.microsoft.com/en-us/windows/wsl/interop).
- Docker, [Bind mounts](https://docs.docker.com/engine/storage/bind-mounts/), [`none` network driver](https://docs.docker.com/engine/network/drivers/none/), and [Resource constraints](https://docs.docker.com/engine/containers/resource_constraints/).
