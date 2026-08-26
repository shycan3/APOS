# Warnings

- Do not let Local Coder mutate the project directly in 0.1. The safer contract is prompt in, patch out, APOS-validated apply.
- Do not treat model completion text as success. Tests or verification commands are the success signal.
- The current machine does not expose an `ollama` command on PATH, so live Ollama execution has not been verified yet.
