# APOS 1.1 Release Notes

APOS 1.1 is the governed self-evolution baseline for all APOS releases before
2.0. It preserves the 1.0 local development runtime and adds an isolated,
measured, two-review candidate lifecycle.

## Added

- tracked APOS 1.x evolution policy pinned to `v1.1.0`
- strict evolution proposal schema and version boundaries
- isolated candidate creation with Git worktrees
- candidate development through the existing APOS kernel
- trusted lineage, clean-state, policy-hash, immutable-control, and version gates
- required unit-test and fixed benchmark gates
- trusted independent replay of every reported benchmark result branch
- machine-readable evaluation reports and Markdown review packets
- Codex and human review records bound to candidate commit and report hash
- `PROMOTABLE` status with no automatic promotion implementation
- `apos evolution` and `apos evolve` CLI command groups

## Safety Boundary

APOS may create, develop, evaluate, and prepare evidence for a candidate. APOS
cannot merge, tag, deploy, or promote a candidate. Review records are local
attestations; release authority remains with the user and external Git actions.

## Baseline

```text
Release: v1.1.0
Suite: apos-evolution-baseline-1.1
Requirement: PASS, 3/3 tasks, average quality score >= 70.0
Runtime: APOS 1.1.0 with qwen2.5-coder:7b through Ollama HTTP
```

The final captured result path is recorded in `.apos/current.md` after release
verification.

## Deferred

- Cloud Controller planning
- authenticated multi-user review identities
- automatic promotion, intentionally forbidden throughout APOS 1.x
