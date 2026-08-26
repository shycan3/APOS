# Decisions

- APOS 0.1 uses a patch-based Local Coder protocol. The coder returns a unified diff or a JSON permission request; APOS applies patches only after permission validation.
- APOS 0.1 requires human-written TaskSpec files. Automatic TaskSpec generation by a Cloud Controller is reserved for a later version.
- The first implementation uses Python stdlib modules and Git CLI to keep the runtime small and easy to inspect.
