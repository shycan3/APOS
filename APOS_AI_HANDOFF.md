# APOS AI Project Handoff

문서 상태: [IMPLEMENTED] 상태와 [RESEARCHED] 방향을 함께 설명하는 AI-to-AI 인계 문서

작성 기준:

- Repository: `https://github.com/shycan3/APOS`
- Stable master: `dd55f31e1acfd1101adb732cacd3290ad021b273`
- Architecture research branch: `research/p0-3b-architecture`
- Research branch HEAD: `06ed3b28d8591bb4004935d1f6bac372529916f6`
- Handoff branch base: 위 research branch HEAD
- Snapshot date: 2026-08-27

상태 표기:

- **[IMPLEMENTED]** 실제 코드와 테스트에서 확인된 상태
- **[NOT IMPLEMENTED]** 현재 코드에는 존재하지 않는 상태
- **[RESEARCHED]** 설계와 기술 조사는 완료했지만 구현하지 않은 상태
- **[PLANNED]** 향후 후보 또는 권장 순서
- **[REJECTED]** 현재 제품 방향으로 명시적으로 채택하지 않은 상태
- **[UNKNOWN]** 코드와 문서만으로 확정할 수 없거나 실제 운영 검증이 필요한 상태

이 문서에서 `현재`는 특별한 설명이 없으면 stable master `dd55f31...`의 코드를 의미한다. Research branch의 여섯 문서는 현재 구현이 아니라 연구 결과다.

## 1. Purpose of This Document

이 문서는 새로운 AI 계정이나 새로운 ChatGPT 세션이 과거 대화 없이 APOS를 인계받도록 만드는 단일 진입점이다. README처럼 설치법만 전달하는 문서가 아니라 다음 다섯 역할을 동시에 수행한다.

1. APOS가 왜 시작되었고 왜 방향을 바꾸었는지 설명한다.
2. 의도한 제품 정체성과 실제 구현 상태를 분리한다.
3. 이미 발견된 보안 경계와 과장해서는 안 되는 보장을 기록한다.
4. 폐기하거나 보류한 설계를 새 AI가 이유 없이 되살리는 것을 막는다.
5. 다음 개발자가 코드를 읽고 안전하게 작업을 재개할 순서를 제시한다.

가장 중요한 읽기 규칙은 다음과 같다.

> Historical / Intentional Context는 왜 이 방향을 선택했는지를 설명한다. Verified Current State는 현재 코드가 실제로 무엇을 보장하는지를 설명한다. 둘은 절대로 같은 의미가 아니다.

예를 들어 MCP는 장기적으로 유력한 adapter지만 현재 구현되어 있지 않다. Execution tier는 연구되었지만 현재 OS sandbox가 존재한다는 뜻이 아니다.

## 2. Executive Summary

APOS는 처음에 Local LLM을 중심으로 코드를 생성하고 개발 작업을 반복하는 로컬 도구로 시작했다. 실제 저장소에도 Ollama adapter, `CommandPatchCoder`, `Kernel`, benchmark, evolution candidate, 한국어 CLI/CUI가 그 역사를 보존하고 있다.

그러나 상위 AI가 계획, 코드 이해, 주요 구현, 디버깅, 리뷰에서 Local LLM보다 우수하고 GitHub 기반 작업도 직접 수행할 수 있다는 사실이 제품의 전제를 바꾸었다. APOS가 또 하나의 coding AI가 되면 존재 이유가 약하다.

현재의 공식 정체성은 다음과 같다.

> **APOS is a project-scoped local execution runtime that provides controlled access to local project capabilities for higher-level AI systems.**

즉 APOS는 AI의 지능을 대체하지 않는다. 상위 AI가 직접 접근하기 어려운 로컬 filesystem, process, test environment, hardware, installed dependency, local-only state와 장시간 작업을 안전하게 중개하는 것이 목적이다.

현재 구현은 중요한 기반을 갖추었다.

- **[IMPLEMENTED] P0-1:** 논리적 project workspace와 filesystem/secret path 통제
- **[IMPLEMENTED] P0-2:** permission, authorization, audit, bounded `shell=False` execution
- **[IMPLEMENTED] P0-3A:** SQLite task/approval persistence, lifecycle, one-time approval consumption, atomic claim, restart recovery
- **[RESEARCHED] P0-3B 방향:** control-plane convergence, `ExecutionBroker`, Job Object 기반 host control, copy-based staged container, execution tiers

하지만 현재 APOS는 외부의 신뢰할 수 없는 AI에 네트워크로 노출할 수 없다. 진정한 OS-level sandbox가 없고, authenticated human identity가 없으며, audit file은 같은 사용자 권한의 악성 subprocess로부터 보호되지 않는다. 무엇보다 production CLI와 legacy 실행 경로가 아직 modern core에 완전히 수렴하지 않았다.

따라서 다음 작업의 우선순위는 MCP, Discord, GUI가 아니다. **현재 모든 privileged path를 식별하고 `ProjectRuntime -> TaskService -> AuthorizationService -> mandatory dispatcher`라는 하나의 통제면으로 수렴시키는 것**이다.

## 3. Project Identity

### Historical / Intentional Context

APOS라는 이름은 AI Project Operating System을 뜻하지만, 범용 OS나 전권을 가진 autonomous agent를 의미하지 않는다. `Operating System`은 AI 요청과 프로젝트 로컬 능력 사이에서 task, permission, lifecycle, execution, audit를 조정한다는 제품적 비유다.

APOS가 지향하는 장기 흐름은 다음과 같다.

```text
Human
  -> High-level AI
  -> typed task / intent
  -> authenticated APOS adapter
  -> durable task control plane
  -> authorization and human approval
  -> controlled or isolated local execution
  -> verified result / diff / artifact
  -> High-level AI and Human
```

### Verified Current State

- **[IMPLEMENTED]** `src/apos/core/runtime.py`의 `ProjectRuntime`은 한 프로젝트에 묶인 composition root다.
- **[IMPLEMENTED]** `ProjectWorkspace`, `FileSystemService`, `PermissionEngine`, `AuthorizationService`, `AuditLog`, `ControlledExecutionService`, `TaskService`, `ToolRegistry`를 조립한다.
- **[IMPLEMENTED]** `TaskService`는 P0-3A가 지정한 공식 task control plane이다.
- **[IMPLEMENTED]** core API는 transport-neutral하다. `ToolResult`와 service method는 특정 MCP/GUI/Discord transport에 종속되지 않는다.
- **[NOT IMPLEMENTED]** production CLI가 `ProjectRuntime`을 사용하지 않는다.
- **[NOT IMPLEMENTED]** mandatory capability dispatcher, OS sandbox, remote protocol, MCP, Discord, GUI, authenticated approval은 없다.

APOS를 다음과 같이 표현하면 안 된다.

- [REJECTED] 범용 autonomous agent
- [REJECTED] unrestricted self-modification system
- [REJECTED] arbitrary remote shell 또는 internet-facing RCE service
- [REJECTED] ChatGPT의 대체 coding model
- [REJECTED] Local LLM launcher가 제품의 중심이라는 설명

## 4. Original Motivation

### Historical / Intentional Context

초기 구상은 사용자의 로컬 컴퓨터에서 Local LLM을 적극적으로 활용하는 개발 환경이었다.

```text
User
  -> APOS
  -> Local LLM
  -> code generation
  -> patch/test/retry
```

이 구상은 실제 저장소 구조에 반영되었다.

- `src/apos/ollama.py`: Ollama HTTP/CLI 호출과 응답 protocol 복구
- `src/apos/coder.py`: 외부 coder command를 실행해 patch 또는 file replacement를 받는 `CommandPatchCoder`
- `src/apos/kernel.py`: coder, permission, patch, tests, rollback을 반복하는 legacy `Kernel`
- `src/apos/benchmark.py`: Local LLM coding 결과를 비교하는 benchmark
- `src/apos/evolution.py`: 별도 candidate branch/workspace를 만들고 평가·리뷰하는 self-evolution prototype
- `src/apos/orchestrator.py`: evolution 흐름을 안내하는 CUI
- `src/apos/cli.py`: 한국어 명령과 위 prototype 기능을 노출하는 실제 entry point

Git history에도 이 단계가 드러난다.

- `1df27b6` (`v1.0.0`): APOS 1.0 baseline 문서화
- `be34ae0`, `928587a` (`v1.1.0`): governed self-evolution baseline과 evidence
- `90f6d80`, `943056d`, `472dee1` (`v1.1.1`): 한국어 console interface와 release
- 여러 `apos/benchmark/...`와 `apos/evolution/...` branch: Local LLM/자가발전 실험의 흔적

이 구현은 버려야 할 실패작이라는 뜻이 아니다. APOS가 실제 로컬 작업, branch 격리, patch 적용, test loop, evidence와 review를 탐색한 중요한 prototype이다. 다만 장기 core의 권한 경계로 그대로 승격할 수는 없다.

## 5. Critical Strategic Pivot

### Historical / Intentional Context

전략적 전환점은 다음 질문이었다.

> 상위 AI가 이미 더 잘 계획하고, 코드를 이해하고, 직접 구현하고, GitHub workflow까지 수행할 수 있다면 왜 APOS가 Local LLM 중심 coding assistant여야 하는가?

단순한 흐름만 필요하다면 다음으로 충분할 수 있다.

```text
User -> ChatGPT -> code generation/review -> GitHub
```

이 경우 APOS가 Local LLM을 한 번 더 호출하는 것은 품질을 낮추고 context handoff, protocol parsing, hardware 요구량, 디버깅 복잡성만 늘릴 수 있다. Local LLM이 중심인 아키텍처는 상위 AI와 경쟁하거나 중복된다.

따라서 APOS의 질문을 `누가 코드를 더 잘 쓰는가`에서 `상위 AI가 로컬에서 무엇을 안전하게 할 수 없는가`로 바꾸었다.

### Decision

- [REJECTED] APOS가 ChatGPT와 경쟁하는 코드 생성 AI가 되는 방향
- [REJECTED] Local LLM을 모든 task의 기본 작성자로 강제하는 방향
- [ADOPTED] 상위 AI가 planning/reasoning/major implementation을 담당할 수 있는 구조
- [ADOPTED] APOS가 local capability와 execution evidence를 제공하는 구조
- [ADOPTED] Local LLM을 optional, narrow, proposal-only resource로 낮추는 구조

`ARCHITECTURE_AUDIT.md`는 이 전환을 repository 수준에서 처음 명확히 했다. 해당 문서는 기존 main control flow가 Local LLM을 정상적인 code author로 가정하는 점을 문제로 보고 CLI/MCP/API/GUI를 교체 가능한 adapter로, Local LLM을 subordinate worker로 재분류했다.

## 6. Why APOS Still Exists

상위 AI만으로 이미 잘 할 수 있는 작업은 다음과 같다.

- architecture와 product planning
- repository reasoning과 documentation
- code generation과 review
- committed source를 이용한 GitHub workflow
- cloud-compatible CI와 conceptual debugging

반면 APOS가 필요한 영역은 로컬 상태와 실제 실행이다.

- 아직 push하지 않은 source, untracked file, generated state 접근
- 사용자의 실제 dependency, compiler, database, service, device 사용
- Windows/driver/hardware 특화 test
- 로컬 process의 timeout, cancellation, crash recovery
- 장시간 task persistence와 reconnect 후 상태 확인
- local-only model 또는 private resource 사용
- 검토된 patch를 실제 working tree에 적용
- local Git status/diff/checkpoint와 실제 환경 evidence 제공

APOS의 고유 가치는 `더 똑똑한 AI`가 아니라 다음 문장에 있다.

> **상위 AI의 실행 가능 범위를 사용자의 로컬 프로젝트까지 확장하되, 그 확장을 project scope, authority, approval, isolation, audit, recovery로 제한한다.**

이 기준은 future feature를 평가하는 scope filter다.

1. High-level AI나 GitHub만으로 충분한가?
2. 실제 로컬 접근이나 durable local execution이 필요한가?
3. APOS가 추가되면서 권한 경계와 failure mode가 더 명확해지는가?

첫 번째만 참이면 APOS core에 넣지 않는 편이 낫다.

## 7. High-level AI vs APOS vs Local LLM

### High-level AI

권장 책임:

- user goal 이해와 architecture reasoning
- repository context 해석
- task decomposition과 계획
- 주요 코드 작성과 복잡한 디버깅
- candidate patch 및 test strategy 제안
- 결과 해석과 다음 작업 결정
- security trade-off 설명과 human approval 요청

상위 AI가 task를 만들었다고 해서 execution authority 또는 human approval authority를 얻지는 않는다. AI output은 schema를 만족해도 untrusted proposal이다.

### APOS runtime

권장 책임:

- 하나의 canonical project에 요청을 bind
- caller, task, attempt, capability, source revision을 durable하게 기록
- permission policy와 approval requirement 평가
- 실제 human approval을 별도 증거로 소비
- filesystem/process/Git/local-model capability를 중개
- execution resource, output, timeout, cancellation, recovery 관리
- result, diff, artifact와 audit evidence 반환
- ambiguous failure를 성공 또는 자동 retry로 숨기지 않음

APOS는 상위 AI의 semantic planner를 복제하지 않는다.

### Local LLM

권장 책임:

- 작은 범위의 code proposal
- local/private source summarization
- failure classification
- candidate test 생성
- 특정 hardware에서 경제적인 narrow inference

제한:

- [PLANNED] optional model adapter 뒤에서만 사용
- model output은 직접 실행하지 않고 proposal로 처리
- APOS policy, approval, task state를 변경할 권한이 없음
- tool call이 발생해도 다른 AI와 동일한 authorization path 사용
- Ollama endpoint를 remote gateway로 노출하지 않음

현재 `ollama.py`는 HTTP localhost와 CLI fallback을 지원하고 protocol output을 복구한다. 이것은 legacy integration이며 modern `ProjectRuntime`의 local-model capability implementation은 아니다.

### Recommended model split

- 기본값: **High-level AI plans and codes; APOS executes locally.**
- 선택값: **High-level AI plans; Local LLM handles a narrow, measurable subtask.**
- [REJECTED for MVP] 여러 Local LLM을 orchestration하는 model swarm

## 8. Current Architecture

### 8.1 Modern core [IMPLEMENTED]

`src/apos/core`의 실제 책임은 다음과 같다.

| Module | 실제 주요 type | 현재 책임과 한계 |
|---|---|---|
| `result.py` | `ToolResult`, `ToolError`, `ErrorCode` | transport-neutral 결과 envelope |
| `workspace.py` | `ProjectWorkspace`, `SecretPolicy`, `WorkspaceViolation` | project root 등록, 상대 경로 정규화, traversal/secret/link 경계 검사. OS process sandbox는 아님 |
| `filesystem.py` | `FileSystemService` | authorized list/read/atomic write. 삭제 capability는 enum에 있으나 일반 delete service는 현재 core surface에 없음 |
| `permissions.py` | `Actor`, `Capability`, `PermissionRequest`, `ApprovalGrant`, `PermissionDecision`, `PermissionEngine`, `AuthorizationService` | default-deny policy, exact request digest, human approval intent와 deterministic authorization 분리 |
| `audit.py` | `AuditLog`, `AuditEvent`, `Redactor` | JSONL event와 secret redaction. malicious same-user process에 대한 immutable storage는 아님 |
| `execution.py` | `CommandPolicy`, `CommandRequest`, `ResourceLimits`, `NetworkPolicy`, `ControlledExecutionService` | trusted executable resolution, `shell=False`, cwd validation, sanitized env, timeout/cancel/output bounds. OS filesystem/network isolation은 없음 |
| `tasks.py` | `TaskState`, `PersistentTask`, `PersistentApproval`, `SQLiteTaskRepository`, `TaskService` | durable lifecycle, approval issue/consume, atomic execution claim, recovery와 outbox/audit 연결 |
| `runtime.py` | `ProjectRuntime` | 한 project에 대한 composition root. `tasks`를 공식 control plane으로 노출 |
| `tools.py` | `ToolDefinition`, `ToolRegistry` | tool name/schema/capability/risk metadata. handler나 dispatcher가 아님 |
| `commandline.py` | `parse_legacy_command` | legacy command string을 shell 없이 argv로 파싱하는 compatibility helper |

`ProjectRuntime.create()`의 실제 조립 순서는 다음과 같다.

```text
ProjectWorkspace
  -> shared Redactor and AuditLog
  -> private-by-convention SQLiteTaskRepository
  -> TaskService
  -> PermissionEngine(approval_consumer=TaskService)
  -> AuthorizationService
  -> FileSystemService
  -> ControlledExecutionService
  -> TaskService._bind_execution_service(...)
  -> metadata-only core_tool_registry()
```

P0-3A review fix 이후 다음 상태도 검증된다.

- `ProjectRuntime`에 public `task_repository` attribute가 없다.
- `TaskService`의 repository는 `_repository` private-by-convention이다.
- `TaskRepository`와 `SQLiteTaskRepository`는 top-level `apos.core`에서 export하지 않는다.
- repository의 일반 transition으로 `APPROVED`와 `RUNNING` 진입을 대체할 수 없다.

Python 내부의 private convention은 malicious in-process code에 대한 security boundary가 아니다. 이것은 adapter 실수와 accidental bypass를 줄이는 architecture boundary다.

### 8.2 Current dependency graph

```text
P0-1 logical workspace/filesystem boundary
  -> P0-2 permission + authorization + audit + controlled execution
    -> P0-3A persistent task + approval + lifecycle control plane
      -> [RESEARCHED] mandatory dispatcher + ExecutionBroker + isolation providers
        -> [PLANNED] authenticated remote adapters
```

P0-2나 P0-3A는 P0-1과 독립적인 기능 묶음이 아니다. resource identity는 workspace에 의존하고, task approval은 exact permission request에 묶이며, process launch는 task approval consumption 뒤에 일어난다.

### 8.3 Test evidence

현재 test suite에는 `unittest` 형식의 test method 119개가 있다. 핵심 파일은 다음과 같다.

- `tests/test_core_runtime.py`: runtime composition, explicit policy requirement, repository non-exposure
- `tests/test_core_filesystem.py`: traversal, absolute path, secret, symlink/alias escape, atomic write
- `tests/test_core_security.py`: default deny, request binding, AI/SYSTEM approval 거부, fail-closed authenticated human, audit/redaction
- `tests/test_core_execution.py`: shell metacharacter, shell syntax, PATH/project hijack, junction cwd, timeout, cancellation, child process cleanup, output bounds, environment, network request separation
- `tests/test_core_tasks.py`: persistence, lifecycle, expiry, one-time consumption, concurrent claim, restart recovery, actor ownership convention, audit, runtime integration
- `tests/test_kernel.py`, `test_ollama.py`, `test_orchestrator.py`, `test_evolution.py`: legacy/prototype behavior와 compatibility

주의: 두 test group이 모두 통과해도 legacy path가 modern core에 수렴했다는 뜻은 아니다. 각각의 독립 동작을 검증할 뿐이다.

## 9. Completed Development History

### P0-1

상태: **[IMPLEMENTED]**

목적은 APOS operation이 무제한 host path가 아니라 등록된 project root를 기준으로 동작하게 만드는 것이었다.

구현된 핵심:

- `ProjectWorkspace.register()`의 canonical project root와 project ID
- project-relative path normalization
- absolute path와 `..` traversal 거부
- secret/internal path deny policy
- symlink와 junction alias를 통한 escape 방어
- `FileSystemService`의 authorized list/read/atomic write
- stable `ToolResult` metadata

실제 보장:

- core filesystem API를 사용하는 cooperative caller에 대해 논리적 project boundary를 제공한다.

보장하지 않는 것:

- 같은 사용자 권한으로 실행된 Python/Node/PowerShell 등의 OS filesystem 접근
- legacy module의 직접 `Path`, `open`, subprocess 사용
- true filesystem namespace 또는 OS sandbox

### P0-2

상태: **[IMPLEMENTED]**

주요 checkpoint: `a259520` (`checkpoint: preserve APOS P0-2 implementation`)

목적은 privileged operation을 `permission -> authorization -> audit -> bounded operation`으로 연결하는 modern core를 만드는 것이었다.

구현된 핵심:

- `Capability`, `ActorKind`, `RiskLevel`, `Decision`
- missing rule이 deny되는 `StaticPermissionPolicy`
- exact `PermissionRequest.digest()`에 묶인 `ApprovalGrant`
- deterministic `PermissionDecision`
- `AuthorizationService` lifecycle audit
- redacted JSONL `AuditLog`
- `CommandPolicy`의 explicit trusted executable set
- `ControlledExecutionService`의 `shell=False`
- cwd validation, environment sanitation, timeout, external cancellation, bounded stdout/stderr
- process start/finish/failure audit
- network capability를 별도 authorization request로 표현

중요한 보안 개선:

- modern execution과 legacy command parser에서 `shell=True` 의존을 제거했다.
- shell metacharacter가 executable shell syntax로 해석되지 않도록 했다.
- project-local executable/PATH hijack을 제한했다.

중요한 한계:

- `NetworkPolicy`의 effective result는 코드에 명시된 대로 `DECLARATIVE_ONLY`다.
- `ResourceLimits.memory_limit_bytes`와 `process_count_limit`는 결과에서 `enforced=False`다.
- trusted interpreter 내부의 filesystem/network/subprocess/OS API는 제한되지 않는다.
- production CLI는 modern runtime을 사용하지 않는다.

### P0-3A

상태: **[IMPLEMENTED]**

주요 commits:

- `741b19f`: 최초 P0-3A checkpoint
- `dd55f31`: architecture/approval boundary review fixes, 현재 stable master

실제 `TaskState`는 다음과 같다.

```text
CREATED -> QUEUED -> WAITING_APPROVAL -> APPROVED -> RUNNING
                                                   -> SUCCEEDED
                                                   -> FAILED
                                                   -> CANCELLED
RUNNING -> RECOVERY_REQUIRED -> FAILED or CANCELLED
```

각 중간 상태에는 명시된 cancel/expire transition이 있으며 terminal state는 다시 시작할 수 없다.

구현된 핵심:

- SQLite schema version과 task/request/approval/outbox persistence
- duplicate task ID와 invalid transition 거부
- approval subject, project, request ID, request digest, task ID matching
- approval expiration과 one-time consumption
- approval consumption과 task claim의 transaction/concurrency control
- 두 worker가 같은 task를 두 번 실행하지 못하도록 atomic claim
- startup 시 `RUNNING -> RECOVERY_REQUIRED`
- 자동 재실행 금지
- task/approval lifecycle event를 audit log로 flush
- secret material persistence 방어와 corruption detection
- `TaskService.run_command_task()`가 approval을 process start 전에 소비

Review fix에서 확정된 의미:

- `TaskService`가 공식 task control plane이다.
- repository는 infrastructure이며 external adapter surface가 아니다.
- `ApprovalGrant`는 human-originated intent만 표현한다.
- `PermissionDecision`은 system policy result이며 approval이 아니다.
- AI와 SYSTEM actor는 human approval을 만들 수 없다.
- `AUTHENTICATED_HUMAN`은 identity proof가 구현되지 않았으므로 fail closed다.

## 10. Current Security Model

현재 security model은 다음 네 층으로 이해해야 한다.

### Logical project policy [IMPLEMENTED]

- canonical project root
- project-relative resource naming
- secret/internal path policy
- core filesystem operation의 authorization

### Capability authorization [IMPLEMENTED]

- explicit capability와 risk
- default deny
- exact request digest
- policy decision과 human intent 분리
- task-bound persistent approval consumption

### Controlled host execution [IMPLEMENTED]

- explicit trusted executable resolution
- `shell=False`
- safe cwd, reduced environment, no stdin
- timeout, cancellation, output limit
- lifecycle audit와 redaction

### Durable task lifecycle [IMPLEMENTED]

- explicit state machine
- SQLite persistence
- atomic approval consumption/claim
- restart recovery without automatic replay

이 네 층은 유용하지만 OS isolation, authenticated remote authority, immutable audit를 제공하지 않는다.

### Current trust assumptions

- APOS host user와 APOS process는 trusted computing base다.
- core service caller가 해당 service boundary를 우회하지 않는다고 가정한다.
- allowlisted executable은 launch 전 identity만 제한할 뿐 launch 후 behavior는 신뢰한다.
- local unauthenticated user approval은 실제 human identity proof가 아니다.
- repository, generated code, Local LLM output, process output은 잠재적으로 untrusted다.

## 11. Known Security Limitations

`SECURITY_THREAT_MODEL.md`의 최종 결론을 그대로 유지해야 한다.

### Can APOS currently be safely exposed to an untrusted external AI over a network?

**NO.**

근거:

- authenticated remote principal과 human identity proof가 없다.
- mandatory dispatcher가 없어 legacy/direct privileged path가 남아 있다.
- current execution은 host user 권한이다.
- network deny가 OS 수준에서 enforce되지 않는다.
- audit storage가 malicious same-user process에서 보호되지 않는다.

### Can APOS currently provide a true OS-level project sandbox?

**NO.**

`ProjectWorkspace`와 `FileSystemService`는 logical API boundary다. process token, filesystem namespace, registry, COM, network stack, native API를 격리하지 않는다.

### Can a trusted interpreter potentially escape the APOS logical project boundary?

**YES.**

Python, Node.js, PowerShell, `cmd.exe`, Git, package manager는 interpreter 또는 process launcher가 될 수 있다. `shell=False`는 parent가 command shell로 문자열을 재해석하지 않게 할 뿐 child가 shell/OS API를 호출하는 것을 막지 않는다.

### Does every privileged operation pass through AuthorizationService?

**NO, not system-wide.**

Core `FileSystemService`와 `ControlledExecutionService`는 통과하지만 production CLI/Kernel/Git/evolution/Ollama/direct filesystem path는 전체적으로 수렴하지 않았다.

### Additional known limitations

- `AuditLog` append API는 accidental mutation을 줄이지만 file-level append-only나 tamper evidence가 아니다.
- TOCTOU가 `workspace.resolve -> authorization -> actual operation/launch` 사이에 남을 수 있다.
- Windows symlink, junction, reparse point, executable replacement, PATH/PATHEXT와 process tree edge case는 계속 threat surface다.
- timeout/cancellation은 best-effort process tree cleanup이며 Job Object 기반 hard process containment가 아니다.
- current actor ID는 authentication credential이 아니다.
- private-by-convention repository는 malicious in-process Python을 막지 못한다.

현재 APOS를 internet-facing API, untrusted remote AI, arbitrary command platform으로 사용하지 않는다.

## 12. Legacy and Modern Architecture Boundary

### Modern control plane

공식 장기 방향:

```text
Adapter
  -> ProjectRuntime.tasks / TaskService
  -> permission and approval
  -> mandatory dispatcher [not implemented]
  -> filesystem/execution/Git/model service
  -> audit and durable result
```

### Legacy/prototype plane

현재 실제 CLI 흐름:

```text
apos CLI
  -> Kernel / GitClient / evolution / orchestrator / draft
  -> CommandPatchCoder / Ollama
  -> legacy PermissionManager / pathing
  -> executor.run_commands / direct subprocess / direct filesystem
```

실제 import와 호출 관계:

- `cli.py`는 `Kernel`, `GitClient`, draft, benchmark, evolution을 직접 import한다.
- `Kernel`은 `CommandPatchCoder`, legacy `run_commands`, `GitClient`, legacy `PermissionManager`, `RunRecorder`를 사용한다.
- `executor.py`는 `parse_legacy_command` 후 `subprocess.run(..., shell=False)`을 호출하지만 modern authorization/task lifecycle을 사용하지 않는다.
- `coder.py`는 configured coder process를 직접 subprocess로 실행한다.
- `ollama.py`는 localhost HTTP 또는 Ollama CLI subprocess를 호출한다.
- `git.py`는 Git subprocess와 branch/patch/commit operation을 직접 제공한다.
- `evolution.py`는 `Kernel`, `GitClient`, direct filesystem/subprocess를 조합한다.
- `orchestrator.py`는 이 legacy evolution workflow의 CUI다.

### Relationship rule

Legacy 기능을 즉시 삭제하거나 기존 사용자 동작을 무시하지 않는다. 먼저 다음 중 하나로 명확하게 분류한다.

1. modern core service 뒤로 migrate
2. optional Local LLM adapter로 subordinate
3. development-only prototype로 quarantine
4. 별도 검토 후 archive/remove

새 adapter가 legacy class를 직접 호출하는 것은 금지한다. 특히 `ToolRegistry`를 dispatcher라고 오해해서 metadata만 보고 handler를 직접 연결하면 안 된다.

## 13. Mobile / Remote Control Vision

상태: **[PLANNED]**

원하는 사용자 경험:

```text
Mobile user
  -> ChatGPT / high-level AI
  -> "APOS 프로젝트 테스트를 실행해줘"
  -> local APOS laptop
  -> authorized and isolated test
  -> durable status/result
  -> ChatGPT
  -> Mobile user
```

또는 bug fix의 경우:

```text
goal -> local inspection -> candidate patch -> staged tests
     -> diff/evidence -> human apply approval -> optional local checkpoint
```

### Researched connectivity direction

직접 inbound laptop API 대신 outbound relay를 우선 검토했다.

```text
High-level AI / mobile client
  -> cloud coordination queue
  <- outbound authenticated connection from local APOS
  -> local protocol gateway
  -> TaskService and dispatcher
```

장점:

- laptop에 일반 public inbound port가 필요하지 않다.
- offline request와 reconnect status를 durable queue로 표현할 수 있다.
- MCP, GitHub, Discord 등 여러 adapter가 같은 protocol을 사용할 수 있다.

아직 해결해야 할 것:

- human/device authentication과 project pairing
- signed/expiring message, nonce, replay/idempotency storage
- task owner/operator mapping
- offline cancel의 정확한 semantics
- relay compromise와 result confidentiality
- 운영 비용과 실제 사용자 가치

이 구조는 [RESEARCHED]이며 구현된 API가 아니다.

## 14. Discord Decision

상태: **[RESEARCHED, NOT REQUIRED FOR MVP]**

Discord가 가능한 역할:

- task notification
- concise status
- approval challenge를 여는 버튼
- narrow slash command

Discord가 맡으면 안 되는 역할:

- raw shell command transport
- bot token만으로 human approval 생성
- channel/display name으로 project authority 추론
- full logs, secrets, local paths 보관
- direct execution/filesystem dispatcher

결론:

> Discord is an optional adapter, not APOS core.

relay, identity, authority, replay protection, approval ceremony가 먼저다. Discord-first architecture를 다시 제안하지 않는다.

## 15. MCP Decision

상태: **[RESEARCHED, STRATEGICALLY RELEVANT, NOT IMPLEMENTED]**

MCP가 적합한 이유:

- high-level AI에 typed tool schema 제공
- task submit/status/cancel/diff 같은 intention-level operation 표현
- ChatGPT-facing adapter 가능성
- transport-neutral core와 궁합이 좋음

MCP가 해결하지 않는 것:

- laptop connectivity
- OS sandbox
- human authentication
- task ownership
- approval binding
- provider isolation
- prompt injection

도입 전 조건:

- production control-plane convergence
- mandatory dispatcher
- authenticated principal과 project grant
- real human approval ceremony
- execution isolation과 effective guarantee reporting
- replay/idempotency와 durable task protocol
- adapter가 core/legacy service를 우회하지 못하는 test

MCP tool은 `run_command`보다 `submit_task`, `get_task`, `cancel_task`, `get_diff`, `request_apply` 같은 coarse operation을 우선해야 한다.

## 16. Execution Isolation Direction

상태: **[RESEARCHED]**, 구현 전 재검토 필요

### Core decision

하나의 universal sandbox를 모든 작업에 강제하지 않는다. 먼저 모든 execution이 immutable manifest와 하나의 `ExecutionBroker`를 통과하게 하고, 실제 OS mechanism은 provider로 분리한다.

연구된 profile:

- **E0 Inspect only:** execution 없음
- **E1 Trusted host tool:** host process, Windows Job Object 기반 tree/resource control, filesystem/network는 cooperative
- **E2 Staged restricted native worker:** low-privilege account/token + ACL staging + Job Object, 필요 시 AppContainer/firewall 연구
- **E3 Staged container:** copy-in, internal writable layer, no network default, non-root/resource limits, patch/artifact copy-out
- **E4 Disposable VM:** Windows-specific/high-risk workload에 Windows Sandbox 또는 Hyper-V copy-in/out

### Recommended first combination

1. Job Object로 강화한 E1 trusted host execution
2. generated/untrusted Linux-compatible code를 위한 copy-based E3 staged container

### Non-claims

- Job Object는 filesystem/network sandbox가 아니다.
- restricted token alone은 namespace가 아니다.
- WSL alone은 Windows drive/interop 때문에 sandbox가 아니다.
- Docker bind mount는 기본 writable이며 source repository를 직접 mount하면 review boundary가 약해진다.
- Windows Sandbox는 강하지만 install/boot/automation/result extraction 비용 때문에 기본 provider로 결정하지 않았다.

### Required execution invariant

```text
authenticated principal
  -> task authority
  -> task claim
  -> permission and bound approval
  -> immutable execution manifest
  -> ExecutionBroker
  -> IsolationProvider
  -> verified result/artifact
  -> audit and durable task state
```

Provider가 source repository를 직접 변경하지 않게 한다. execution output 적용은 별도 authorized operation이어야 한다.

연구 설계는 과잉설계 가능성을 포함한다. 실제 workload와 설치 환경을 측정해 E2/E4를 나중으로 미루는 판단은 허용된다. 그러나 security claim을 낮추지 않고 기능만 늘리는 타협은 허용되지 않는다.

## 17. Git Responsibility Model

Git은 APOS의 central intelligence나 sandbox가 아니다. project state를 식별하고 candidate change를 검토·복구하는 service다.

권장 capability 분리:

- `GIT_READ`: status, diff, revision identity
- `GIT_WRITE`: branch/worktree/checkpoint 같은 local mutation
- `GIT_RESET`: destructive operation, 기본 deny
- `GIT_PUSH`: network + credential + remote mutation, 별도 승인

권장 workflow:

1. execution 전 `HEAD`와 dirty-state fingerprint 기록
2. APOS-owned staged copy 또는 worktree에서 candidate 변경
3. test 실행
4. diff와 evidence 제시
5. 별도 authorization으로 실제 project에 apply
6. race/revision 재검증
7. 필요하면 local checkpoint commit
8. 별도 network/credential approval 후 optional remote push

중요한 결정:

- local checkpoint와 remote push는 같은 권한이 아니다.
- 자동 push는 기본값이 아니다.
- force push, hard reset, clean, history rewrite는 MVP 범위가 아니다.
- rollback은 처음부터 `git reset --hard`가 아니라 revert proposal 또는 APOS-owned artifact/worktree 복구로 시작한다.
- linked worktree는 workflow isolation이지 hostile-code sandbox가 아니다.

현재 `src/apos/git.py`의 legacy `GitClient`가 이 장기 responsibility model을 구현했다고 간주하지 않는다.

## 18. Task Authority Model

상태: **[RESEARCHED]**

향후 principal과 authority를 분리한다.

| Role | 책임 |
|---|---|
| Task owner | task intent와 지속적 소유, status/cancel/delegation의 기준 |
| Task operator | manifest 제안, approval 요청, monitoring/cancel/retry 요청 |
| Task observer | 허용된 status/result의 read-only view |
| Approval authority | authenticated human으로서 특정 operation을 one-time 승인 |
| Execution worker | exact authorized manifest를 atomically claim하고 실행 |
| Recovery authority | crash/lease/artifact/Git ambiguity를 안전하게 해소 |

핵심 규칙:

- task creator와 owner는 같지 않을 수 있다.
- AI가 task를 만들었다고 human approval authority를 얻지 않는다.
- adapter service identity와 end-user identity는 다르다.
- worker lease는 client connection이 아니라 durable attempt에 묶인다.
- retry는 새 attempt이며 기존 approval 재사용을 기본으로 하지 않는다.
- automated recovery는 deterministic non-destructive reconciliation만 수행한다.

MVP에서는 복잡한 enterprise RBAC를 만들지 않는다. 한 명의 authenticated human owner/admin/approver, paired local device/worker, AI proposer/operator 정도의 최소 모델로 시작한다.

## 19. Current Research Branch and Architecture Research

Research branch:

- `research/p0-3b-architecture`
- HEAD `06ed3b28d8591bb4004935d1f6bac372529916f6`
- Commit: `docs: add P0-3B architecture research and long-term roadmap`

문서:

1. `P0_3B_ARCHITECTURE_DECISION.md`
   - Windows execution technology 비교
   - E0-E4 profiles
   - broker/provider contract와 rejected alternatives
2. `APOS_LONG_TERM_ARCHITECTURE.md`
   - product identity, actual module split, AI/APOS/Local LLM responsibility
   - data/control flow와 MVP
3. `REMOTE_CONTROL_ARCHITECTURE.md`
   - Discord, MCP, GitHub, tunnel, desktop/mobile, outbound relay 비교
4. `TASK_AUTHORITY_MODEL.md`
   - owner/operator/observer/approval/execution/recovery authority
5. `UPDATED_ROADMAP.md`
   - dependency-driven development phases와 MVP boundary
6. `FUTURE_THREAT_MODEL.md`
   - adapter, replay, task hijack, Local LLM, supply chain, container, network, log threats

이 문서들은 Microsoft, Docker, MCP, OpenAI, GitHub, Discord, Ollama의 공식 자료를 근거로 작성되었다. 그러나 branch merge나 implementation approval을 자동으로 의미하지 않는다. 새 AI는 실제 코드와 workload를 기준으로 비판적으로 검토하되, 이미 확인된 non-guarantee를 무시해서는 안 된다.

## 20. Current Roadmap

### Phase 1: Control-plane convergence [NEXT]

목적:

- modern core를 production privileged path의 유일한 진입점으로 만든다.

범위:

- mandatory capability dispatcher 설계
- CLI/CUI를 `ProjectRuntime`과 `TaskService` 위로 이동
- Kernel/executor/coder/Git/evolution/Ollama의 direct privileged path를 migrate 또는 quarantine
- core/legacy permission과 path policy의 중복 제거 전략
- 허용된 subprocess/filesystem mutation site를 architecture test로 고정

### Phase 2: Execution manifest, broker, E1 [PLANNED]

- immutable execution/result manifest
- attempt lease와 recovery
- staged snapshot/artifact contract
- Job Object process tree/resource enforcement
- effective guarantee reporting

### Phase 3: E3 staged container [PLANNED]

- pinned image
- non-root, no network default, explicit limits
- source copy-in과 patch/artifact copy-out
- no Docker socket/privileged/source writable mount

### Phase 4: Identity, authority, audit [PLANNED, REMOTE GATE]

- human/device/adapter/worker principal
- project grant와 task role
- challenge-bound authenticated approval
- replay/idempotency
- cross-process audit integrity와 protected storage/export

### Phase 5: Local protocol and outbound relay [PLANNED]

- typed/versioned local gateway
- device/project pairing
- outbound connection, durable lease/ack
- offline/reconnect/cancel semantics

### Phase 6: ChatGPT/MCP adapter and local approval UI [PLANNED, MVP COMPLETION]

- narrow task/status/cancel/diff tools
- trusted local approval/recovery surface
- end-to-end mobile test/build/patch workflow

### Post-MVP

- E2/E4 Windows-native isolation providers where demand exists
- typed Git service and optional local checkpoint
- optional Local LLM accelerator
- GitHub/Discord/native mobile adapters if justified
- controlled evolution only after independent isolation, Git, evidence, and review gates mature

## 21. MVP Boundary

APOS MVP는 다음 end-to-end 경험이 가능할 때 완성된다.

```text
Trusted mobile/high-level AI interface
  -> one registered local project
  -> durable task submission
  -> authenticated human approval when required
  -> E1 trusted or E3 staged execution
  -> progress, cancellation, restart recovery
  -> bounded logs and verified result/diff/artifact
  -> separate apply approval
  -> optional local checkpoint
```

MVP가 제공해야 하는 핵심 가치:

- local-only project capability를 remote AI가 안전하게 요청
- task가 conversation disconnect와 process restart를 견딤
- human approval과 AI intent가 분리됨
- generated code가 직접 source tree를 변경하지 않음
- result가 실제 source revision과 execution evidence에 묶임

MVP에 포함하지 않는 것:

- Discord 필수화
- arbitrary shell/RCE
- autonomous self-modification
- unrestricted OS control
- multi-project global agent
- mandatory Local LLM
- model swarm
- automatic GitHub push
- destructive Git rollback
- 모든 workload에 VM 강제

## 22. Explicitly Rejected Directions

### [REJECTED] Local LLM-first architecture

상위 AI보다 낮은 품질의 model을 모든 작업의 필수 경로로 두면 context split, latency, hardware, retry, parsing 복잡성이 커진다. Local LLM은 optional capability다.

### [REJECTED] Discord-first architecture

Discord는 identity, authority, isolation, durable control plane을 해결하지 않는다. 먼저 core와 relay를 만든다.

### [REJECTED] GUI-first architecture

GUI가 backend invariant보다 먼저 생기면 private repository/direct process call을 UI에 고착시킬 수 있다. 로컬 approval/recovery UI는 필요하지만 backend 뒤에 온다.

### [REJECTED] Direct public local API

현재 runtime을 tunnel이나 public endpoint로 노출하면 trusted interpreter가 사실상 host-user RCE가 될 수 있다. outbound relay 또는 동등하게 제한된 gateway를 검토한다.

### [REJECTED] Arbitrary shell and universal OS automation

APOS의 scope는 project capability다. unrestricted shell/desktop/OS administration은 제품 목표가 아니다.

### [REJECTED] AI or SYSTEM self-approval

human approval intent는 deterministic system authorization과 다르다. AI/SYSTEM은 이를 생성할 수 없다.

### [REJECTED] Job Object as a complete sandbox

process tree와 resource control에는 유용하지만 filesystem/network isolation이 없다.

### [REJECTED] WSL or restricted token alone as proof of isolation

각각 host interop와 ACL-based limitation 때문에 true project sandbox를 증명하지 않는다.

### [REJECTED] Direct writable source mount for untrusted execution

execution과 artifact review/apply를 분리할 수 없고 concurrent mutation과 untracked data loss 위험이 있다.

### [REJECTED for now] Unrestricted autonomous self-evolution

기존 evolution prototype은 historical asset이다. Running APOS가 스스로 승인·통합·배포하는 구조는 금지한다. 미래 controlled evolution도 clone/worktree, isolation, benchmark, external review, explicit promotion이 필요하다.

## 23. Decisions Already Made

새 AI는 다음 결정을 기본 전제로 삼는다. 변경하려면 새로운 evidence와 명시적 architectural review가 필요하다.

1. APOS는 ChatGPT와 경쟁하는 coding AI가 아니다.
2. High-level AI가 planning, reasoning, major code generation을 담당할 수 있다.
3. APOS는 project-scoped local capability execution layer다.
4. Local LLM은 optional capability다.
5. Discord는 core가 아니며 MVP dependency가 아니다.
6. MCP는 전략적으로 유력하지만 현재 implementation priority가 아니다.
7. 현재 APOS를 untrusted remote AI에 노출하지 않는다.
8. 현재 OS-level sandbox가 없음을 숨기지 않는다.
9. security claim은 실제 effective enforcement보다 강할 수 없다.
10. privileged operation은 하나의 control plane과 dispatcher로 수렴해야 한다.
11. `TaskService`가 공식 task control plane이다.
12. `ToolRegistry`는 metadata registry이지 dispatcher가 아니다.
13. human approval과 `PermissionDecision`은 다른 개념이다.
14. AI/SYSTEM은 human approval을 생성할 수 없다.
15. authenticated human identity가 구현되기 전에는 fail closed다.
16. execution과 source artifact application은 분리한다.
17. local checkpoint와 remote push는 별도 capability/approval이다.
18. project-scoped operation을 우선하며 multi-project global authority를 기본으로 하지 않는다.
19. crash ambiguity는 recovery state로 남기고 자동 재실행하지 않는다.
20. legacy behavior를 보존할 필요와 official security architecture를 혼동하지 않는다.

## 24. Open Questions

다음은 아직 확정된 구현 답이 없는 질문이다.

### Control-plane migration

- 기존 CLI command 중 어떤 것을 modern runtime에 그대로 보존할 것인가?
- `Kernel`을 optional Local LLM workflow로 migrate할 것인가, quarantine할 것인가?
- mandatory dispatcher의 최소 API와 handler registration 방식은 무엇인가?
- architecture test로 허용할 direct subprocess/filesystem site를 어떻게 고정할 것인가?

### Execution isolation

- 실제 사용자 project 중 E3 Linux container로 실행 가능한 비율은 얼마인가?
- Windows-native test를 위해 E2 low-priv worker가 충분한가, E4 VM이 필요한가?
- Docker Desktop 설치와 backend 상태를 product prerequisite로 받아들일 수 있는가?
- Job Object binding과 executable identity TOCTOU를 Python/Windows에서 어떻게 fail closed로 구현할 것인가?
- staging과 artifact apply에서 untracked/dirty file을 어떻게 정확히 fingerprint할 것인가?

### Identity and remote control

- single-user MVP의 identity provider와 step-up approval UX는 무엇인가?
- outbound relay를 직접 운영할지 existing platform을 활용할지?
- relay가 result를 읽지 못하게 end-to-end encryption이 필요한가?
- ChatGPT-facing MCP/plugin의 실제 product availability와 mobile UX가 요구를 충족하는가?
- GitHub App가 초기 asynchronous adapter로 MCP보다 먼저 가치가 있는가?

### Audit and recovery

- SQLite/task writer와 JSONL audit writer를 한 process owner로 고정할 것인가?
- audit hash chain을 어디에 독립적으로 anchor할 것인가?
- process/provider crash 후 정확한 external effect를 어떻게 reconcile할 것인가?
- remote cancellation acknowledgement와 actual termination을 UI에서 어떻게 구분할 것인가?

### Product scope

- 실제 사용자에게 가장 먼저 가치 있는 registered workflow는 test, build, patch 중 무엇인가?
- local checkpoint를 MVP 필수로 볼 것인가 optional로 둘 것인가?
- Local LLM이 품질/비용/latency에서 measurable benefit을 주는 narrow task가 존재하는가?

이 질문을 답할 때 architecture research document를 결론이 아니라 hypothesis와 constraint로 사용한다.

## 25. Instructions for a New AI

### Start-up checklist

1. `git remote -v`, current branch, `git status`, `git rev-parse HEAD`를 확인한다.
2. stable master `dd55f31...`과 research `06ed3b2...`가 원격과 일치하는지 확인한다.
3. 이 문서를 읽고 `ARCHITECTURE_AUDIT.md`, `SECURITY_THREAT_MODEL.md`, `P0_2_IMPLEMENTATION.md`, `P0_3A_IMPLEMENTATION.md`를 읽는다.
4. research branch의 여섯 문서를 읽는다.
5. 문서 주장과 actual code/import/call path를 다시 대조한다.
6. 기존 tests를 실행하고 baseline failure가 있으면 구현 전에 기록한다.

### Development rules

- 사용자의 최신 지시 없이 master에 직접 구현하거나 push하지 않는다.
- 기존 working tree 변경을 임의로 삭제하거나 덮어쓰지 않는다.
- feature마다 purpose, authority boundary, security implication, failure mode, recovery/rollback, test strategy를 먼저 정의한다.
- 새 adapter가 legacy privileged class를 직접 import하지 않게 한다.
- 새 capability는 `TaskService`, authorization, dispatcher, audit를 우회할 수 없게 한다.
- transport authentication을 human approval이나 task authority로 간주하지 않는다.
- request policy와 OS enforcement를 구분해 결과에 effective guarantee를 기록한다.
- model/repository/process output을 untrusted data로 취급한다.
- destructive Git operation과 remote push는 별도 명시적 승인 없이는 수행하지 않는다.
- architecture와 security가 바뀌면 문서와 threat model을 같은 review cycle에서 갱신한다.

### Decision discipline

- 과거 결정을 맹목적으로 따르지 않는다.
- 그러나 이미 reject된 방향을 다시 제안하려면 기존 이유를 직접 반박하는 evidence가 필요하다.
- 기능이 멋져 보이는지보다 APOS의 고유한 local capability 역할에 필요한지 판단한다.
- 완전한 sandbox가 아니면 `sandboxed`라는 이름을 사용하지 않는다.
- partial implementation을 완료된 product capability로 표현하지 않는다.

### Before any implementation

반드시 다음 질문에 답한다.

1. 이 기능은 High-level AI/GitHub만으로 충분한가?
2. 어떤 local-only capability를 제공하는가?
3. caller, owner, operator, approver, worker는 누구인가?
4. 실패하거나 중단되면 durable state는 무엇인가?
5. malicious repository/model/process output이 어디에 들어오는가?
6. source와 credential에 대한 실제 OS boundary는 무엇인가?
7. 어떤 테스트가 bypass와 recovery를 증명하는가?

## 26. Immediate Recommended Next Step

새 AI가 가장 먼저 해야 할 일은 P0-3B sandbox code를 바로 작성하는 것이 아니다.

### Recommended task

> **Production Privileged Path Convergence Review and Dispatcher Contract**

목표:

1. `cli.py`의 모든 command에서 filesystem, subprocess, Git, Ollama, evolution으로 이어지는 실제 call graph를 완성한다.
2. 각 privileged operation을 modern core service로 migrate, optional adapter로 subordinate, prototype로 quarantine 중 하나로 분류한다.
3. `ToolRegistry`와 분리된 mandatory dispatcher contract를 최소 범위로 설계한다.
4. CLI의 한 개 read-only 또는 low-risk workflow를 end-to-end `ProjectRuntime -> TaskService -> authorization -> service -> audit`로 통과시키는 migration slice를 제안한다.
5. direct privileged imports/calls를 탐지하는 architecture test strategy를 정의한다.

첫 implementation slice를 시작하기 전 completion criteria:

- production/legacy call graph가 문서화되어 있다.
- bypass list와 허용된 infrastructure call site가 명시되어 있다.
- dispatcher가 task authority와 approval을 어떻게 받는지 정의되어 있다.
- migration 중 기존 CLI behavior를 어떻게 보존할지 정해져 있다.
- test, failure, rollback plan이 있다.

그 다음에만 최소한의 control-plane convergence를 구현한다. ExecutionBroker, Job Object, container, MCP, Discord, GUI, remote API를 같은 change에 섞지 않는다.

이 순서는 보수적으로 보일 수 있지만 APOS의 가장 큰 현재 위험인 `modern core가 존재하지만 실제 제품 경로가 우회할 수 있음`을 먼저 해결한다. 이 경계를 닫아야 이후 isolation과 remote adapter가 실제 보안 개선이 된다.
