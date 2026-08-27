# APOS

APOS is a local orchestration runtime for letting AI coding agents work inside a
project with explicit task contracts, restricted write scope, verification, retry,
Git history, and living documentation.

## 한국어 빠른 시작

APOS 1.2부터 사람이 보는 CLI 도움말과 일반 출력은 기본적으로
한글입니다. 명령 이름과 `--json` 데이터 구조는 자동화 호환성을 위해
기존 영문 규약을 유지합니다.

```powershell
cd C:\Users\DH\Documents\ChatGPT\APOS_PROJECT
apos --help
apos status
apos evolution status
```

### CUI 오케스트레이터 (v1.2.0 신규)

APOS 1.2에서는 대화형 CUI 오케스트레이터가 추가되었습니다.
복잡한 명령어를 외울 필요 없이, 번호 선택으로 자기 진화 전체 절차를
진행할 수 있습니다.

```powershell
apos              # TTY 환경에서 바로 오케스트레이터 진입
apos evolve       # 오케스트레이터 직접 진입 (동일)
```

오케스트레이터 메뉴:

```text
[다음으로 할 수 있는 일]
  1. 원스톱 자기 진화 가이드 (개선안 -> 후보 생성 -> 개발 -> 평가 -> 검수 안내)
  2. 새 진화 제안서(Proposal) 작성 및 후보 등록
  3. 기존 후보 개발 실행 (apos evolution run)
  4. 후보 상태 평가 (apos evolution evaluate)
  5. 검수 결과 기록 (Codex / Human 승인 또는 반려)
  6. 상태 및 정책 검증 조회
  0. 종료
```

**1번 메뉴(원스톱 가이드)**를 선택하면 APOS가 다음 순서로 안내합니다:

1. 개선 목표 및 목표 버전 입력
2. 자동 제안서 생성 및 격리 후보 복제본 생성
3. 로컬 코더로 후보 개발 실행
4. 신뢰된 기준선(단위테스트 + 벤치마크)으로 후보 평가
5. Codex / Human 검수 기록
6. 승격 가능(PROMOTABLE) 시 수동 머지/태그 안내

> **안전 장치**: APOS는 자동 머지, 태그, 승격을 절대 수행하지 않습니다.
> 모든 릴리스 작업은 사용자가 외부 Git 터미널에서 직접 수행합니다.

### 기존 CLI 방식 (직접 명령어 사용)

기존처럼 PowerShell에서 개별 명령을 직접 호출할 수도 있습니다:

```powershell
apos evolution create <제안서.json> --candidate-id <후보-ID>
apos evolution run <후보-ID>
apos evolution evaluate <후보-ID>
apos evolution review <후보-ID> --reviewer codex --decision approve --note "검수 근거"
apos evolution review <후보-ID> --reviewer human --decision approve --note "사용자 승인"
apos evolution status <후보-ID>
```

`PROMOTABLE`은 승격 가능한 상태라는 뜻이며, 실제 병합과 태그는 여전히
사용자 통제 아래에서 수행합니다.

## APOS 1.1

APOS 1.1 combines the proven 1.0 task runtime with a governed self-evolution
loop for every release before 2.0:

```text
TaskSpec
-> permission validation
-> patch or controlled file replacement from a Local Coder
-> test commands
-> retry loop
-> Git commit
-> run log, quality report, benchmark result
```

APOS 1.1 adds a second lifecycle:

```text
versioned proposal
-> isolated candidate worktree
-> APOS development loop
-> trusted tests and fixed benchmark
-> commit-bound Codex review
-> commit-bound human review
-> PROMOTABLE (manual promotion only)
```

APOS 1.1 does not include a Cloud Controller yet. A human, draft command, or
refine command prepares the TaskSpec, and APOS runs the implementation loop
against a local coder command.
If a patch applies but verification fails, APOS rolls that patch back before the
next retry so attempts do not accumulate broken intermediate edits.

## Install for development

```bash
python -m pip install -e .
```

## Initialize a project

```bash
apos bootstrap
apos init
```

This creates `.apos/` project memory files and a default config.
Use `bootstrap` when setting up a new project quickly; it can also configure
Ollama in the same step:

```bash
apos bootstrap --ollama-model qwen2.5-coder:7b
```

## Configure a Local Coder

APOS 1.1 expects a local coder command that reads a prompt from stdin and writes
either:

- a unified diff patch to stdout
- a JSON file replacement for one allowed file
- or a JSON permission request

```bash
apos connect-ollama --model qwen2.5-coder:7b
```

This requires Ollama to be installed and available on `PATH`, or passed with
`--ollama-binary`. APOS uses Ollama's HTTP API first for cleaner machine output
and falls back to the CLI runner if HTTP is unavailable.

You can also connect any command directly:

```bash
apos connect --coder-command "python path/to/my_coder.py"
```

Or set `APOS_CODER_COMMAND`.

## Run a task

Draft a TaskSpec from explicit inputs:

```bash
apos draft "Add greeting behavior" --allow src/app/greeting.py --test "python -m unittest"
```

Refine an existing TaskSpec with the configured Ollama model:

```bash
apos refine tasks/task-001.json
```

```bash
apos run examples/task-spec.sample.json
```

For testing the loop without creating a commit:

```bash
apos run examples/task-spec.sample.json --no-commit --allow-dirty
```

If a Local Coder is expected to ask for extra context or write scope, pre-approve
or pre-deny those requests:

```bash
apos run tasks/task-001.json --approve-read src/app/config.py
apos run tasks/task-001.json --approve-write src/app/generated.py
apos run tasks/task-001.json --deny-permission secrets.env
```

Each run writes inspectable artifacts under `.apos/runs/<task-id>/<run-id>/`,
including the TaskSpec, attempt prompts, coder responses, test results, and
final summary. APOS automatically excludes `.apos/runs/` from Git tracking for
the local repository.
If the verification commands already pass before a coder is invoked, APOS
records a preflight PASS run without requesting a patch.

## Inspect runs

```bash
apos runs list
apos runs show .apos/runs/task-001/20260826T010000Z-abc12345
apos report .apos/runs/task-001/20260826T010000Z-abc12345
```

Use `--json` with these commands when another tool needs structured run data.
The report command produces a compact quality summary with status, attempts,
test counts, rollback counts, failure classification, commit information, and a
deterministic score.

## Benchmark suites

```bash
apos benchmark validate examples/benchmarks/fast-track-suite.json
apos benchmark show examples/benchmarks/fast-track-suite.json
apos benchmark run examples/benchmarks/fast-track-suite.json
apos benchmark run examples/benchmarks/fast-track-suite.json --approve-read src/app/config.py
```

A benchmark suite groups TaskSpec files with comparison metadata such as
category, difficulty, weight, tags, and metrics.
Benchmark run results are written under `.apos/benchmarks/<suite-id>/<run-id>/`
and include each task summary, quality report, failure summary, and runner
profile metadata.

```bash
apos benchmark results list
apos benchmark results show .apos/benchmarks/apos-fast-track-0-1/<run-id>/result.json
apos benchmark compare \
  .apos/benchmarks/apos-fast-track-0-1/<run-a>/result.json \
  .apos/benchmarks/apos-fast-track-0-1/<run-b>/result.json
```

The comparison command ranks benchmark results by average quality score,
completed task pass count, and total task duration so different APOS profiles or
external runner imports can be judged side by side.

## TaskSpec shape

```json
{
  "task_id": "TASK-001",
  "title": "Add greeting behavior",
  "goal": "Update the greeting function while preserving the public API.",
  "allowed_files": ["src/app/greeting.py", "tests/test_greeting.py"],
  "read_only_files": ["src/app/APOS.md"],
  "constraints": ["Do not change the public function name."],
  "expected_behavior": ["greet('APOS') returns 'Hello, APOS!'"],
  "test_commands": ["python -m pytest tests/test_greeting.py"],
  "context_requirements": []
}
```

## Local Coder protocol

The coder command receives a complete implementation prompt on stdin. It should
return only one of the following.

Raw unified diff:

```diff
diff --git a/src/app/greeting.py b/src/app/greeting.py
--- a/src/app/greeting.py
+++ b/src/app/greeting.py
@@ -1,2 +1,2 @@
 def greet(name):
-    return name
+    return f"Hello, {name}!"
```

JSON file replacement:

```json
{
  "type": "file_replacement",
  "path": "src/app/greeting.py",
  "content": "def greet(name):\n    return f\"Hello, {name}!\"\n"
}
```

JSON permission request:

```json
{
  "type": "request_permission",
  "path": "src/app/config.py",
  "permission": "read",
  "reason": "The greeting behavior depends on a configured prefix."
}
```

Permission escalation is supported through pre-approved or pre-denied run
options in APOS 1.1.

## Governed self-evolution

The complete safety contract and lifecycle are documented in
`SELF_EVOLUTION.md`. Start by validating the immutable 1.1 baseline:

```bash
apos evolution validate
apos evolution status
```

Create and develop a candidate from a bounded proposal:

```bash
apos evolution create examples/evolution/proposal-1.2.sample.json --candidate-id planning-1-2
apos evolution run planning-1-2
apos evolution evaluate planning-1-2
```

Only a full passing evaluation can receive reviews. Both reviews are bound to
the evaluated commit and report hash:

```bash
apos evolution review planning-1-2 --reviewer codex --decision approve --note "Reviewed diff and evidence."
apos evolution review planning-1-2 --reviewer human --decision approve --note "Approved for manual promotion."
```

APOS can report `PROMOTABLE`, but it cannot merge, tag, deploy, or promote
itself. Those actions remain under external user control.

## Evolution baseline 1.1

The fixed APOS 1.x control command is:

```bash
apos benchmark run examples/benchmarks/fast-track-suite.json --keep-going --max-attempts 5
```

Required threshold:

```text
PASS, 3/3 tasks, average quality score >= 70.0
```

Captured APOS 1.1 result:

```text
.apos/benchmarks/apos-evolution-baseline-1-1/20260826T184408Z-b8ae3d4f/result.json
PASS, 3/3 tasks, average quality score 90.0, trusted replay PASS
```
