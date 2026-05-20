---
Role: system_core
Mode: STRICT
Persistence: permanent
---

# APOS v3.1 RC SYSTEM CORE

AI Project Operating System

--------------------------------------------------

## Status

Release Candidate

Purpose:

Stable enough for long-term experimental usage.

Further refinement may occur through actual project use.

--------------------------------------------------

## Core Philosophy

- Do not eliminate failure.
- Make failure traceable.
- Separate temporary and permanent information.
- Load only relevant context.
- Human approval overrides automation.
- Sustainability overrides rigid rules.

--------------------------------------------------

## Suggested Initialization Sequence

When available or instructed:

1. Read `.apos/system_core.md`
2. Read `.apos/session_state.md`
3. Load `.apos/risk_vector.json`
4. Load relevant files from `specifications/`
5. Read `context/decisions.md`
6. Read `workspace/current_tasks.md`

--------------------------------------------------

## Specification Selection Priority

1. Files directly referenced by current task

2. Files referenced by active decisions

3. Files referenced by current tasks

4. Core specifications:

- core_direction.md
- immutable_rules.md

--------------------------------------------------

## Context Isolation Rule

STRICT MODE

Include:

- relevant files from specifications
- context/decisions.md
- workspace/current_tasks.md
- technical workspace files

Exclude:

- creative drafts
- speculative notes
- worldbuilding fragments
- obsolete scratch content

CREATIVE MODE

Include:

- relevant files from specifications
- context/decisions.md
- context/project_history.md
- creative workspace files

Exclude:

- debugging logs
- obsolete technical artifacts
- unrelated implementation notes

--------------------------------------------------

## Risk Queue Protocol

Risk Queue:

max_queue_limit: 5

overflow_policy:

archive_resolved_then_request_approval

Severity:

LOW

- formatting
- typo fixes
- cosmetic naming

MEDIUM

- local implementation changes
- refactoring

HIGH

- architecture
- schema changes
- API contracts
- irreversible deletion
- permanent lore changes

Queue behavior:

1. Archive resolved risks

2. If queue remains full:

- suggest lowest-priority risks for archival

3. Keep all HIGH risks active

4. Ask user approval before archiving unresolved risks

--------------------------------------------------

## Sync Execution Rule

If file write capability exists:

- update files directly
- report changed files

If file write capability does not exist:

- output replacement content
- specify file paths
- do not claim sync completion until user confirms overwrite

--------------------------------------------------

## Metadata Header Guideline

Metadata headers are strongly recommended for:

- active workspace files
- long-lived drafts
- reusable artifacts

Optional for:

- temporary experiments
- quick debugging files
- disposable notes

Template:

---
Role: technical_workspace
Mode: STRICT
Persistence: temporary
---

Allowed values:

Mode:

- STRICT
- CREATIVE

Persistence:

- temporary
- permanent

Permanent persistence should normally exist only inside:

- specifications/
- context/
