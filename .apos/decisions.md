# Decisions

- APOS 1.0 uses a controlled Local Coder protocol. The coder returns a unified diff, JSON file replacement, or JSON permission request; APOS applies changes only after permission validation.
- APOS 1.0 can draft and refine TaskSpec files, while automatic Cloud Controller planning is reserved for a later version.
- The first implementation uses Python stdlib modules and Git CLI to keep the runtime small and easy to inspect.
- APOS 1.1 is the permanent governance baseline for APOS 1.x. Every accepted candidate must descend from `v1.1.0` and remain below version 2.0.0.
- Self-evolution candidates run in dedicated Git worktrees and branches. The trusted workspace evaluates candidates as external targets.
- The policy, benchmark suite, benchmark TaskSpecs, and benchmark verification scripts are immutable candidate controls.
- A full evaluation and commit-bound approvals from both `codex` and `human` are required before a candidate can be reported as `PROMOTABLE`.
- APOS 1.x has no merge, tag, deploy, or automatic promotion operation. The user remains the release authority.
