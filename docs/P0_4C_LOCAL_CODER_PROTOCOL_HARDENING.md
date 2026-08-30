# P0-4C Local Coder Protocol Hardening

P0-4C hardens the boundary between local model output and APOS protocol parsing. It keeps the existing APOS execution architecture unchanged:

```text
Local model output
    -> apos.ollama
    -> protocol extraction
    -> parse_coder_output()
    -> Kernel
    -> controlled Git / controlled tests
```

## Problem Observed

Real `qwen2.5-coder:7b` probes showed two protocol failures before APOS reached controlled patch execution:

- context-style diff output such as `1c1`, `< old`, `---`, `> new`
- prose explanation plus a Python Markdown code block

Neither shape is a valid APOS local coder response. APOS should not guess target files or convert arbitrary code blocks into writes.

## Chosen Strategy

`apos.ollama` now performs one bounded protocol-format repair attempt when the initial model output cannot be extracted as an APOS response.

The normal generation path is unchanged. If the first output is already valid, no repair happens.

If the first output is invalid, the adapter sends a repair prompt asking the model to return exactly one JSON protocol object:

- `{"type":"patch","patch":"valid unified diff patch"}`
- `{"type":"file_replacement","path":"path/from/task.allowed_files","content":"complete final file text"}`
- `{"type":"request_permission","path":"path","permission":"read","reason":"why the context is required"}`

For HTTP generation, the repair request uses Ollama JSON schema format mode to constrain the response to an APOS protocol object with a top-level `type` field. If HTTP generation is unavailable, the existing CLI fallback remains available.

## Preserved Protocol Formats

The existing accepted formats remain supported:

- raw unified diff
- fenced unified diff
- JSON patch
- JSON file replacement
- JSON permission request

## Unsafe Outputs

Arbitrary Python, JavaScript, or other code blocks are not interpreted as file writes. The repair prompt may ask the model to transform the original task into an allowed protocol response, but APOS still requires the repaired output to pass normal protocol extraction and parsing.

Context diffs remain rejected. APOS does not convert `1c1`/`<`/`>` style diffs into unified diffs because doing that safely would require a separate deterministic conversion design.

Malformed output remains a safe failure. If the repair output is also invalid, `apos.ollama` exits with the existing protocol failure path.

## Retry Behavior

The repair behavior is deliberately narrow:

- maximum one repair attempt
- only triggered by protocol-format extraction failure
- does not retry Git apply failures
- does not retry test failures
- does not retry APOS execution failures
- does not recursively retry repair failures

## Timeout Considerations

No timeout behavior was changed in P0-4C. The outer APOS coder timeout still bounds the `python -m apos.ollama` subprocess during normal APOS runs. The Ollama adapter still passes its configured timeout to HTTP and CLI generation calls.

The known timeout layering remains:

```text
RunOptions.command_timeout_seconds
    -> Kernel
    -> CommandPatchCoder subprocess timeout
    -> python -m apos.ollama
    -> Ollama HTTP or CLI timeout
```

Broader timeout architecture changes are deferred.

## Deferred Work

P0-4C does not add:

- arbitrary code-block conversion
- context diff conversion
- broad retry infrastructure
- automatic patch repair after Git failures
- automatic repair after test failures
- MCP changes
- Application Service or Kernel redesign
