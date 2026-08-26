# Current State

- APOS 0.1 is implemented as a Python package in `src/apos`.
- The CLI exposes `init`, `connect`, `status`, `validate`, `task-template`, and `run`.
- The core runtime accepts a human-written TaskSpec, requests a unified diff from a configured Local Coder command, validates changed paths against `allowed_files`, applies the patch, runs test commands, retries on failure, and can commit successful changes.
- Each run writes inspectable artifacts under `.apos/runs/<task-id>/<run-id>/`, including prompts, coder responses, test results, and summary JSON.
- Failed patches that apply but do not pass verification are reverse-applied before the next retry.
- Ollama 0.32.15 is installed locally and `qwen2.5-coder:7b` is available as the configured Local Coder model.
- Cloud Controller planning is not implemented in 0.1.
