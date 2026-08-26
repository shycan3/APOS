# Current State

- APOS 0.1 is implemented as a Python package in `src/apos`.
- The CLI exposes `init`, `connect`, `status`, `validate`, `task-template`, and `run`.
- The core runtime accepts a human-written TaskSpec, requests a unified diff from a configured Local Coder command, validates changed paths against `allowed_files`, applies the patch, runs test commands, retries on failure, and can commit successful changes.
- Ollama can be configured as a Local Coder with `apos connect-ollama --model <model>`.
- Cloud Controller planning is not implemented in 0.1.
