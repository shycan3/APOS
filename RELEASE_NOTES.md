# APOS 1.0 Release Notes

APOS 1.0 is the fast-track local release for competitive AI coding experiments.

## Included

- TaskSpec validation, draft, and Ollama-assisted refinement
- Local Coder orchestration with explicit file permissions
- Unified diff, controlled file replacement, and permission request responses
- Test execution, retries, rollback, and Git commits
- Inspectable run logs and quality reports
- Benchmark suites, result inspection, runner profiles, and result comparison
- Pre-approved and pre-denied permission decisions
- Project bootstrap and Ollama configuration

## Baseline

```text
Command: apos benchmark run examples/benchmarks/fast-track-suite.json --keep-going --max-attempts 5
Result: PASS, 3/3 tasks, average quality score 70.0
Path: .apos/benchmarks/apos-fast-track-1-0/20260826T181653Z-0b1a7959/result.json
Runtime: APOS 1.0.0 with qwen2.5-coder:7b through Ollama HTTP
```

## Deferred

- Cloud Controller planning
- APOS self-evolution workflow
- Automatic promotion review gates
