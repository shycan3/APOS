# Decisions

- APOS 1.0 uses a controlled Local Coder protocol. The coder returns a unified diff, JSON file replacement, or JSON permission request; APOS applies changes only after permission validation.
- APOS 1.0 can draft and refine TaskSpec files, while automatic Cloud Controller planning is reserved for a later version.
- The first implementation uses Python stdlib modules and Git CLI to keep the runtime small and easy to inspect.
