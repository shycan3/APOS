# Warnings

- Do not let Local Coder mutate the project directly in 1.0. The safer contract is prompt in, controlled response out, APOS-validated apply.
- Do not treat model completion text as success. Tests or verification commands are the success signal.
- The current shell does not expose `ollama` on PATH, so APOS config uses the absolute Ollama executable path.
