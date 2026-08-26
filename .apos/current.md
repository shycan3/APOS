# Current State

- APOS 0.1 is implemented as a Python package in `src/apos`.
- The CLI exposes `init`, `connect`, `status`, `validate`, `task-template`, and `run`.
- The core runtime accepts a human-written TaskSpec, requests a unified diff from a configured Local Coder command, validates changed paths against `allowed_files`, applies the patch, runs test commands, retries on failure, and can commit successful changes.
- Ollama 0.32.15 is installed locally and `qwen2.5-coder:7b` is available as the configured Local Coder model.
- Cloud Controller planning is not implemented in 0.1.
