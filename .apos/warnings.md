# Warnings

- Do not let Local Coder mutate the project directly. The contract is prompt in, controlled response out, APOS-validated apply.
- Do not treat model completion text as success. Tests or verification commands are the success signal.
- The current shell does not expose `ollama` on PATH, so APOS config uses the absolute Ollama executable path.
- Do not run candidate evaluation from candidate code. The trusted 1.1 workspace must own policy loading, gate decisions, and review evidence.
- Review records are local attestations, not reviewer identity authentication.
- Never add automatic candidate promotion to APOS 1.x.
