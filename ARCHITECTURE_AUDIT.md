# APOS Repository Audit and Migration Baseline

Status: repository audit completed against the redefined product architecture

Audit date: 2026-08-27

Audited revision: `472dee1` (`v1.1.1`) plus the uncommitted APOS 1.2 CUI draft

## 1. Decision

APOS is now defined as a **project-scoped local AI runtime / execution bridge**:

> External AI performs reasoning, planning, coding, review, and prioritization.
> APOS validates, authorizes, executes, records, and returns operations against one
> explicitly registered local project.

The current repository is not yet that product. It is a working local-coder task
runner plus a governed self-evolution system. Several implementation ideas are
valuable, but the main control flow incorrectly assumes that a Local LLM is the
primary code author and that APOS self-evolution is a primary user workflow.

The migration must therefore preserve proven low-level mechanisms while replacing
the product center. APOS 1.2 must not be released from the current working tree.

## 2. Repository State

- Latest completed release: `v1.1.1` at `472dee1`
- Runtime language: Python 3.10+
- Runtime dependencies: Python standard library only
- Packaging: setuptools, editable local installation supported
- Source package: `src/apos`
- Test framework: `unittest`
- Audit-start test inventory: 63 tests including the uncommitted CUI tests
- Audit-start test result: 62 passed, 1 failed in 186.426 seconds
- `pytest` is not installed and is not a declared dependency
- `compileall` passes
- `git diff --check` reports no whitespace errors

The working tree contains an uncommitted APOS 1.2 CUI draft:

- modified: `README.md`, `SELF_EVOLUTION.md`, `pyproject.toml`
- modified: `src/apos/__init__.py`, `src/apos/cli.py`
- modified tests: `tests/test_benchmark.py`, `tests/test_cli_korean.py`
- untracked: `src/apos/orchestrator.py`, `tests/test_orchestrator.py`

These changes are preserved as pre-existing work. They are not an accepted release
or the basis of the new architecture.

## 3. Current Architecture

```text
CLI (cli.py, 1,022 lines)
 |-- project bootstrap/config
 |-- TaskSpec draft/refine
 |-- task run/report
 |-- benchmark commands
 `-- self-evolution commands and CUI draft
       |
       +--> evolution.py --> benchmark.py --> kernel.py
       |                                      |
       `--------------------------------------+
                                              |
Kernel --> Local Coder --> patch/replacement  |
   |          |                               |
   |          `--> Ollama or arbitrary command
   |                                          |
   +--> write-scope permission validation     |
   +--> shell test commands ------------------+
   +--> Git branch/apply/commit
   `--> run artifacts and quality report
```

The dependency direction is mostly inward from CLI to implementation modules, but
there is no transport-neutral application service or stable tool interface. The
`Kernel` is coupled to Local Coder invocation, Git branching, patch application,
test execution, retry, logging, and commit behavior in one lifecycle.

### Current component map

| Component | Current responsibility | New-architecture assessment |
|---|---|---|
| `cli.py` | All command parsing, localization, rendering, and dispatch | Too large; retain only as a thin adapter |
| `models.py` | Local-coder TaskSpec, permissions, execution and run results | Useful data types, but not the required operational Task model |
| `pathing.py` | Relative path normalization and resolved-root check | Strong starting point for Project Workspace |
| `permissions.py` | Patch write-path validation | Too narrow; read and execute capabilities are not enforced |
| `executor.py` | Runs arbitrary shell strings with timeout | Unsafe for an external-AI bridge without command policy/isolation |
| `git.py` | Local Git CLI wrapper, patch, branch, commit | Retain and wrap with explicit capability policy and structured results |
| `kernel.py` | Local LLM task loop and rollback | Move behind an optional legacy/local-LLM worker adapter |
| `coder.py` | Arbitrary coder process and patch protocol | Optional Local LLM subsystem, not core runtime |
| `ollama.py` | Ollama HTTP/CLI adapter | Retain as an optional provider adapter |
| `runlog.py` | Task prompts, responses, test output, rollback artifacts | Useful basis, but unsafe as a general audit log without redaction |
| `config.py` | `.apos` setup, local coder config, narrative memory files | Replace with project, policy, adapter, and operational-state config |
| `draft.py` | Human/Ollama TaskSpec generation | External AI concern; remove from core product flow |
| `benchmark.py` | Local-coder quality benchmarks | Development tooling, not APOS runtime core |
| `report.py` | Heuristic local-coder quality scoring | Development tooling, not runtime result semantics |
| `evolution.py` | APOS candidate worktrees, evaluation, review records | Release-development tooling; not a core user capability |
| `orchestrator.py` | Interactive self-evolution CUI | Conflicts with the new product center; do not promote |

## 4. Implemented Features

The repository currently implements:

- Git-project detection and `.apos` initialization
- Local coder command configuration and Ollama integration
- TaskSpec validation, drafting, and Ollama-assisted refinement
- Explicit file lists for local-coder read and write context
- Unified diff and complete-file replacement protocols
- Patch path validation and canonicalized project path resolution
- Test-command preflight, execution, retry, and timeout
- Rollback of a failed generated patch or file replacement
- Task branch creation and successful-change commit
- Run logs, summaries, quality reports, and benchmark comparison
- Self-evolution proposals, isolated Git worktrees, fixed evaluation gates,
  commit-bound review records, and manual-only promotion
- Korean CLI output
- An uncommitted interactive self-evolution CUI draft

The repository does **not** currently implement the new MVP's general-purpose
`list_files`, `read_file`, `write_file`, `run_command`, `run_tests`, task lifecycle,
permission decisions, structured error envelope, audit stream, or external tool
server as independent capabilities.

## 5. Broken or Incomplete Behavior

### Current working-tree regression

`test_apos_no_args_non_interactive_shows_help` fails. The no-argument CLI enters
the interactive CUI when the inherited stdin is a TTY, including a subprocess
used as non-interactive automation. It then consumes EOF instead of printing help.

### CUI release integrity problems

- The source reports `1.2.0` before the change is committed, evaluated, tagged, or
  released.
- New proposals hard-code `base_ref` to `v1.1.0`, bypassing the latest reviewed
  parent release `v1.1.1`.
- The menu allows a user to record a `codex` approval. Reviewer identity is not
  authenticated, so the evidence does not prove that Codex reviewed the candidate.
- Broad `except Exception` blocks hide policy and state failures.
- The CUI adds 609 source lines and 350 test lines to a workflow that is no longer
  a primary product experience.

### Existing runtime gaps

- `PermissionSpec.execute` is populated but never enforced.
- The permission manager stores readable paths but exposes no read-check API.
- There is no queued/running/waiting/cancelled task state machine.
- Timeout does not provide reliable descendant-process cancellation.
- Rollback covers generated file edits, not arbitrary changes or external side
  effects made by test commands.
- Candidate runtime records remain after promotion and do not have a promoted or
  closed lifecycle state.
- Narrative `.apos` memory files are initialized but are not runtime inputs.

## 6. Security Findings

Severity is assessed against the new threat model in which an external AI can call
APOS tools.

### Critical: command execution is not project-scoped

`executor.py` and the trusted evolution evaluator execute arbitrary command strings
with `shell=True`. Setting `cwd` to the project root does not prevent commands from
reading or changing files elsewhere, using the network, accessing credentials, or
starting privileged child processes.

The configured Local Coder command is also arbitrary host code. Its temporary
working directory reduces accidental repository mutation but provides no OS,
network, secret, or privilege isolation.

### High: secrets can be disclosed and persisted

There is no secret-path deny policy. A TaskSpec may include `.env`, certificates,
tokens, or other sensitive project files. Their complete contents are placed in the
Local Coder prompt and then written again to `.apos/runs/.../prompt.json`.

Test stdout/stderr, patches, replacements, and permission reasons are also stored
without redaction. Ignoring runtime logs in Git does not make cleartext storage safe.

### High: rollback does not cover the execution surface

Test commands and arbitrary coder commands may mutate any project or host path.
APOS only reverses the patch or replacement it applied itself. A reported rollback
therefore does not mean the task's side effects were rolled back.

### High: reviewer identity is forgeable

Any local caller can record either the `codex` or `human` review role. The current
CUI makes this explicit by asking the user whether to record Codex approval. Review
records are commit-bound but not actor-authenticated.

### Medium: some CLI file inputs and outputs escape project policy

`validate`, `run`, and `refine` load TaskSpec paths directly. Evolution proposal
loading also accepts a direct filesystem path. `draft --output` and
`refine --output` join an unchecked `Path` to the root; an absolute output path
therefore writes outside the project. These commands predate the external-tool
threat model but cannot be exposed as bridge tools.

### Medium: path policy is good but incomplete as a security boundary

`project_path` resolves the root and candidate path, preventing common traversal
and existing symlink escapes. Tests cover allowed and unauthorized patch paths,
but do not cover symlinks, junctions, broken symlinks, path races, case behavior,
or filesystem operations such as delete and move.

### Medium: result and audit semantics are inconsistent

Most failures are Python exceptions or localized CLI text. JSON success output is
command-specific and there is no universal `{success, data, error}` envelope.
Current logs do not consistently record requester, tool, normalized arguments,
permission decision, duration, changed files, or redaction metadata.

### Medium: network access is uncontrolled

Shell commands may invoke network clients, and the Ollama host is configurable to
an arbitrary URL. There is no network allow/deny/approval decision.

## 7. Contradictions With the Redefined Product

| Current assumption | Required product behavior |
|---|---|
| Local LLM is the normal code author | External AI writes directly through scoped tools; Local LLM is optional |
| APOS task loop decides retry and completion flow | External AI decides the next action; APOS executes deterministically |
| Self-evolution is a primary CLI/CUI workflow | Self-evolution is release tooling, outside the runtime core |
| TaskSpec is the central API contract | Small capability calls and an operational task lifecycle are central |
| Permission means allowed patch paths | Permission covers file, command, network, secret, Git, and destructive actions |
| `cwd=project` means project-scoped execution | Process and filesystem isolation must be evaluated separately |
| Run artifacts are sufficient audit records | Every capability action needs a redacted, structured audit event |
| CLI is the product interface | CLI, MCP, API, and GUI are replaceable adapters |
| Narrative project memory is core state | Operational project, task, policy, model, and audit state is core |

## 8. Keep, Refactor, Park, Delete

### Keep

- Canonical path resolution concept from `pathing.py`
- Non-shell Git argument execution from `GitClient`
- Dataclass-based typed records
- Patch write-scope checks as defense in depth for the optional Local LLM worker
- Timeout and captured-output concepts
- Isolated Git worktree concept for risky development tasks
- Ollama provider implementation as an optional adapter
- Existing `v1.1.1` tag as the compatibility baseline

### Refactor

- `pathing.py` into a first-class `ProjectWorkspace` authority
- `permissions.py` into capability decisions with allow, deny, and approval-required
- `executor.py` into policy validation, argv-based execution, cancellation, bounded
  output, and explicit isolation metadata
- `git.py` into a project-bound Git service with safe and dangerous operation classes
- `models.py` into separate tool result, task state, permission, and legacy TaskSpec types
- `runlog.py` into append-only, redacted audit events plus task artifacts
- `config.py` into explicit project and policy configuration
- `cli.py` into a thin adapter over the same application service used by MCP/API
- `kernel.py` into an optional Local LLM worker workflow using core capabilities

### Park outside the runtime core

- `benchmark.py`
- `report.py`
- `evolution.py`
- `SELF_EVOLUTION.md`
- benchmark fixtures and evolution policy

These may remain as APOS repository development tools until the new runtime is
stable. They must not define the public product architecture.

### Do not promote; later remove or archive

- The uncommitted `orchestrator.py` self-evolution CUI
- `draft` and `refine` as core user workflows
- Narrative `.apos` memory files that have no operational consumer
- Any implicit claim that APOS itself is the planner or reviewer

Deletion should happen only after the new runtime covers required compatibility
paths and the current uncommitted work is deliberately resolved.

## 9. Target Architecture

```text
External AI / human / automation
              |
       transport adapters
      MCP | API | CLI | GUI
              |
       Application Runtime
              |
   +----------+----------+
   |          |          |
 Project   Tasks      Tool Registry
   |          |          |
   +---- Permission -----+
              |
   +----------+----------+----------+----------+
   |          |          |          |          |
 Files     Process      Tests       Git     Local LLM
   |          |          |          |          |
   +----------+----------+----------+----------+
              |
        Audit and artifacts
```

Dependency rules:

1. Core types and policies depend on no transport or provider.
2. Every operation receives one explicit `ProjectWorkspace` context.
3. Capability services validate project boundary and permission before effects.
4. Adapters translate protocols only; they contain no business rules.
5. Local LLM uses the same core tools and has no privileged path.
6. Audit receives redacted events from every operation.
7. GUI observes and approves; it does not become an AI chat frontend.

## 10. Initial Tool Contract

The first stable application service should expose only:

```text
project.info
fs.list
fs.read
fs.write
exec.run
test.run
git.status
git.diff
task.create
task.get
task.cancel
```

Every response uses one envelope:

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "PERMISSION_REQUIRED",
    "message": "Network access requires approval.",
    "details": {"capability": "network"}
  },
  "meta": {
    "project_id": "...",
    "task_id": "...",
    "audit_id": "..."
  }
}
```

Raw output is stored as an artifact only after redaction. Default tool results are
bounded summaries with an explicit continuation or artifact reference.

## 11. Migration Path

### Stage 0: freeze and characterize

- Keep `v1.1.1` immutable as the last completed legacy release.
- Do not release the current 1.2 CUI draft.
- Preserve the draft until the user deliberately chooses archive or removal.
- Add characterization tests for path, command, Git, and logging behavior.

Exit condition: the legacy baseline and known failures are documented and repeatable.

### Stage 1: establish the new core

- Introduce `ProjectWorkspace` as the only source of project paths.
- Introduce structured result and error types.
- Implement project-bound list/read/write tools.
- Implement secret-path denial before any file content is returned or logged.
- Introduce capability policy decisions: allow, deny, approval required.
- Add a redacted audit event schema.

Exit condition: external code can safely inspect and edit a registered project
through Python APIs without importing CLI, Kernel, Evolution, or Ollama modules.

### Stage 2: controlled execution and local Git

- Replace raw shell execution in the new API with validated argv requests.
- Classify safe, restricted, and dangerous commands.
- Record the actual isolation level; never claim sandboxing when only `cwd` is set.
- Add timeout and process-tree cancellation.
- Expose read-only Git status/diff/log first.
- Add checkpoint and task-scoped rollback before mutating Git operations.

Exit condition: tests and approved commands run with explicit policy and auditable
limitations; local Git state can be inspected and recovered.

### Stage 3: operational tasks and external bridge

- Implement task states: `QUEUED`, `RUNNING`, `WAITING_APPROVAL`, `SUCCEEDED`,
  `FAILED`, `CANCELLED`, and `ROLLED_BACK`.
- Add a provider-neutral tool registry.
- Add an MCP adapter over the application service.
- Keep CLI as a debugging and initialization adapter.

Exit condition: an external AI can inspect, edit, test, and review a local project
using structured tool calls without invoking the legacy Local Coder loop.

### Stage 4: optional Local LLM worker

- Adapt Ollama and the patch protocol to the same scoped core capabilities.
- Remove direct privileged execution paths from `Kernel` and `CommandPatchCoder`.
- Require permission, diff, audit, and rollback for applied Local LLM output.

Exit condition: Local LLM is optional and subordinate to an external caller.

### Stage 5: cleanup and hardening

- Move benchmark and self-evolution code to repository development tooling.
- Archive or delete the CUI draft and obsolete narrative memory behavior.
- Add network, secret, process, and resource controls.
- Add a monitoring/approval GUI only after the runtime contract is stable.

## 12. First P0 Implementation Slice

The next code change should be deliberately small:

1. Add typed `ToolResult` and stable error codes.
2. Add `ProjectWorkspace` with canonical root enforcement and secret-path policy.
3. Add `fs.list`, `fs.read`, and `fs.write` as transport-neutral services.
4. Add focused traversal, absolute-path, symlink, secret, and atomic-write tests.
5. Do not wire these tools through the current CUI or Local Coder kernel yet.

This slice proves the new dependency direction and security boundary before command
execution, task management, MCP, or GUI work begins.

## 13. Release Gate

No version after `v1.1.1` should be tagged until all of the following are true:

- The version is not changed before the candidate is accepted.
- The full test suite passes in a genuinely non-interactive environment.
- New public capabilities return structured results and errors.
- File operations cannot escape the registered project through absolute paths,
  traversal, symlinks, or junctions covered by the platform test matrix.
- Secret paths are denied by default.
- Command execution reports its real containment guarantees.
- Reviewer identity is not represented more strongly than it is authenticated.
- The product documentation describes APOS as an execution bridge, not an agent.

## 14. P0 Slice Implementation Status

The first P0 slice described in section 12 is implemented in the working tree:

- `src/apos/core/result.py`: stable `ToolResult`, `ToolError`, and error codes
- `src/apos/core/workspace.py`: registered canonical project root and default
  secret-path policy
- `src/apos/core/filesystem.py`: transport-neutral list, read, and atomic write
  capabilities
- `tests/test_core_filesystem.py`: absolute path, traversal, symlink/junction escape,
  secret alias, secret file, structured envelope, listing, and atomic-write coverage

The pre-existing no-argument CLI regression was also corrected without making the
CUI a core dependency: `apos` prints help and explicit `apos evolve` remains the
only interactive entry point in the draft.

Final verification after this slice: 71 tests passed in 96.957 seconds. Python
bytecode compilation and `git diff --check` also pass.

## 15. P0-2 Direction

P0-2 is implemented in the working tree and documented in
`P0_2_IMPLEMENTATION.md`. It adds centralized capability decisions, exact and
one-time approval grants, append-only redacted audit events, authorized filesystem
operations, controlled `shell=False` execution, environment sanitization,
process-tree timeout/cancellation, bounded output, a project runtime composition
root, and provider-neutral tool metadata.

The implementation intentionally reports rather than conceals the absence of an
OS-level sandbox. General-purpose interpreter execution is not considered fully
project-contained until P0-3 selects a Windows isolation mechanism.
