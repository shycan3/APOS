# APOS 1.1 Self-Evolution Contract

APOS 1.1 is the permanent governance baseline for every APOS 1.x evolution.
Its purpose is to let APOS improve APOS while keeping candidate work isolated,
measured, reviewable, and unable to promote itself.

## Authority Model

- APOS may create an isolated candidate, implement an approved proposal, run
  tests and benchmarks, and assemble review evidence.
- Codex reviews the candidate diff and its evidence and records an approval or
  rejection bound to the exact candidate commit and evaluation report hash.
- The user records the final human approval and remains the release authority.
- APOS never merges, tags, deploys, or promotes its own candidate.

Review records are local attestations, not identity authentication. Promotion
remains an external, human-controlled Git and release operation.

## Pinned Baseline

The tracked policy is `.apos/evolution-policy.json`. It pins:

- baseline release `v1.1.0`
- candidate version range above `1.1.0` and below `2.0.0`
- required unit-test commands
- the immutable three-task benchmark and minimum score
- immutable policy, benchmark metadata, task definitions, and test controls
- required `codex` and `human` reviews
- disabled automatic promotion

The release capture passed 3/3 tasks with average quality score 90.0, and all
three result branches passed the independent trusted replay.

Every candidate records the policy SHA-256, baseline commit, parent commit,
target version, proposal snapshot, branch, and workspace path at creation time.

## Candidate Lifecycle

1. Write a bounded evolution proposal. The proposal names the reviewed parent
   release, exact target version, writable files, tests, risk, constraints, and
   observable expected behavior.
2. Create a candidate. APOS resolves the baseline and parent commits, verifies
   lineage and version ordering, and creates a dedicated Git worktree and
   `apos/evolution/<candidate-id>` branch.
3. Run candidate development. The normal APOS permission, retry, rollback,
   verification, logging, and commit loop operates only in that worktree.
4. Evaluate the candidate from the trusted workspace. APOS checks lineage,
   clean state, policy hash, immutable controls, version consistency, required
   tests, and the full benchmark. The trusted evaluator then checks out every
   reported benchmark branch and independently reruns its pinned TaskSpec tests.
5. Review the exact evaluated commit. Any code change invalidates the previous
   evaluation and prevents its reviews from being reused.
6. After both reviews approve, APOS reports `PROMOTABLE`. A user-controlled
   external process performs any merge and release tag.

## Commands

```bash
apos evolution validate
apos evolution status
apos evolution create examples/evolution/proposal-1.2.sample.json --candidate-id planning-1-2
apos evolution run planning-1-2
apos evolution evaluate planning-1-2 --quick
apos evolution evaluate planning-1-2
apos evolution review planning-1-2 --reviewer codex --decision approve --note "Reviewed diff and evidence."
apos evolution review planning-1-2 --reviewer human --decision approve --note "Approved for manual promotion."
apos evolution status planning-1-2
```

`--quick` deliberately produces `INCOMPLETE`; it can never be reviewed or
promoted. A full evaluation must produce `READY_FOR_REVIEW` first.

## Invariants Through APOS 1.x

- `v1.1.0` remains an ancestor of every accepted candidate.
- Each new proposal starts from an explicitly named reviewed release ref.
- Version declarations in `pyproject.toml` and `src/apos/__init__.py` agree.
- Candidate versions remain below `2.0.0`.
- The policy and control benchmark cannot be modified by a candidate.
- Candidate benchmark JSON is not trusted until every result branch passes an
  independent replay using the pinned suite and TaskSpecs.
- Required tests and the benchmark pass without lowering thresholds.
- Reviews bind to one candidate commit and one evaluation report hash.
- APOS has no automatic promotion path.

Changing these invariants is a 2.0 governance decision, not a 1.x evolution.
