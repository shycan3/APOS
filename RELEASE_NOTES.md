# APOS 1.1 Release Notes

## APOS 1.1.1

- Korean is now the default language for human-facing CLI help and output.
- Argparse usage headings and common parser errors are localized.
- Windows console output is explicitly UTF-8 to prevent broken Korean text.
- Stored JSON keys, protocol status codes, command names, and options remain unchanged.
- Displayed status values include both Korean meaning and the original code.
- The candidate passed all required tests, the fixed 3/3 benchmark, trusted replay, Codex review, and human approval before manual promotion.

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
Observed: PASS, 3/3 tasks, average quality score 90.0, trusted replay PASS
Result: .apos/benchmarks/apos-evolution-baseline-1-1/20260826T184408Z-b8ae3d4f/result.json
Runtime: APOS 1.1.0 with qwen2.5-coder:7b through Ollama HTTP
```

## Deferred

- Cloud Controller planning
- authenticated multi-user review identities
- automatic promotion, intentionally forbidden throughout APOS 1.x
