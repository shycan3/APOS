# src/apos

## Purpose

Core Python package for the APOS 0.1 runtime.

## Structure

- `cli.py`: command-line interface for init, connect, status, validate, template, and run.
- `models.py`: TaskSpec, PermissionSpec, ExecutionResult, and run summary data structures.
- `kernel.py`: task lifecycle loop that coordinates coder, permissions, tests, retry, and Git.
- `coder.py`: patch-based Local Coder command adapter and prompt builder.
- `ollama.py`: Ollama adapter that wraps APOS prompts and extracts patch protocol output.
- `permissions.py`: write-scope validation for patches and repository changes.
- `git.py`: Git CLI wrapper for branch, diff, patch, and commit operations.
- `executor.py`: verification command execution.
- `config.py`: `.apos/` memory initialization and config loading.
- `pathing.py`: project-relative path normalization and root escape checks.

## Rules

- Local Coder output is treated as data and must be validated before patch application.
- A task succeeds only when verification commands pass.
- Cloud Controller behavior is outside APOS 0.1.
