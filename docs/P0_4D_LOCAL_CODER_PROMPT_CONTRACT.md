# P0-4D Local Coder Prompt Contract

P0-4D hardens the model-facing prompt contract for local coder tasks. It does not change APOS permission enforcement, path normalization, Git execution, test execution, MCP tools, or Ollama protocol repair.

## Observed Failure

The first real APOS self-development experiment reached the configured local coder and returned a protocol-valid permission request instead of a patch:

```text
write apos/mcp_server.py
```

The task allowed:

```text
src/apos/mcp_server.py
tests/test_mcp_server.py
```

APOS correctly treated those paths as distinct and stopped with `NEEDS_PERMISSION`.

## Prompt Contract

The local coder prompt now states that `allowed_files` contains repository-relative filesystem paths that are already approved for modification. A model should return a patch or file replacement directly when modifying those files, and it should not request write permission for them.

The prompt also requires paths in patches, file replacements, and permission requests to be used exactly as listed. The model must not remove, shorten, infer, or rewrite path prefixes.

## Module Names vs Filesystem Paths

Python module names and import paths are different from repository filesystem paths. For example, an import such as `apos.mcp_server` does not authorize or imply the filesystem path `apos/mcp_server.py`. The repository path must come from the task's explicit file lists.

## Unknown Files

If a required change concerns a file outside `allowed_files`, the model should use the normal permission protocol for that exact repository-relative path. APOS should not guess that a similar-looking path corresponds to an allowed file.

## Rejected Direction

P0-4D intentionally does not add alias normalization such as mapping `apos/mcp_server.py` to `src/apos/mcp_server.py`. That would require project-layout knowledge and could be unsafe in repositories with multiple source roots, generated code, namespace packages, or legitimate top-level package paths.

## Remaining Limits

This change improves instruction clarity only. It does not prove that `qwen2.5-coder:7b` can produce a semantically correct patch for arbitrary tasks. The real self-development experiment must be repeated separately after review, commit, and push.
