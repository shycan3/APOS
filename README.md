# APOS

APOS is a local orchestration runtime for letting AI coding agents work inside a
project with explicit task contracts, restricted write scope, verification, retry,
Git history, and living documentation.

## APOS 0.1

The first version intentionally starts small:

```text
human-written TaskSpec
-> permission validation
-> patch-based Local Coder
-> test commands
-> retry loop
-> Git commit
```

APOS 0.1 does not include a Cloud Controller yet. A human writes the TaskSpec,
and APOS runs the implementation loop against a local coder command.
If a patch applies but verification fails, APOS rolls that patch back before the
next retry so attempts do not accumulate broken intermediate edits.

## Install for development

```bash
python -m pip install -e .
```

## Initialize a project

```bash
apos init
```

This creates `.apos/` project memory files and a default config.

## Configure a Local Coder

APOS 0.1 expects a local coder command that reads a prompt from stdin and writes
either:

- a unified diff patch to stdout
- or a JSON permission request

```bash
apos connect-ollama --model qwen2.5-coder:7b
```

This requires Ollama to be installed and available on `PATH`, or passed with
`--ollama-binary`.

You can also connect any command directly:

```bash
apos connect --coder-command "python path/to/my_coder.py"
```

Or set `APOS_CODER_COMMAND`.

## Run a task

```bash
apos run examples/task-spec.sample.json
```

For testing the loop without creating a commit:

```bash
apos run examples/task-spec.sample.json --no-commit --allow-dirty
```

Each run writes inspectable artifacts under `.apos/runs/<task-id>/<run-id>/`,
including the TaskSpec, attempt prompts, coder responses, test results, and
final summary. APOS automatically excludes `.apos/runs/` from Git tracking for
the local repository.

## Inspect runs

```bash
apos runs list
apos runs show .apos/runs/task-001/20260826T010000Z-abc12345
apos report .apos/runs/task-001/20260826T010000Z-abc12345
```

Use `--json` with these commands when another tool needs structured run data.
The report command produces a compact quality summary with status, attempts,
test counts, rollback counts, commit information, and a deterministic score.

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

JSON permission request:

```json
{
  "type": "request_permission",
  "path": "src/app/config.py",
  "permission": "read",
  "reason": "The greeting behavior depends on a configured prefix."
}
```

Permission escalation is detected in 0.1, but approval workflow is intentionally
reserved for a later version.
