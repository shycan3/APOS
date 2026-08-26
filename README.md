# APOS

APOS is a local orchestration runtime for letting AI coding agents work inside a
project with explicit task contracts, restricted write scope, verification, retry,
Git history, and living documentation.

## APOS 1.0

APOS 1.0 is a fast-track local runtime for competitive AI coding experiments:

```text
TaskSpec
-> permission validation
-> patch or controlled file replacement from a Local Coder
-> test commands
-> retry loop
-> Git commit
-> run log, quality report, benchmark result
```

APOS 1.0 does not include a Cloud Controller yet. A human, draft command, or
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

APOS 1.0 expects a local coder command that reads a prompt from stdin and writes
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
options in APOS 1.0.

## Fast-track 1.0 benchmark

The current local baseline is:

```bash
apos benchmark run examples/benchmarks/fast-track-suite.json --keep-going --max-attempts 5
```

Latest captured result:

```text
.apos/benchmarks/apos-fast-track-1-0/20260826T181653Z-0b1a7959/result.json
PASS, 3/3 tasks, average quality score 70.0
```
