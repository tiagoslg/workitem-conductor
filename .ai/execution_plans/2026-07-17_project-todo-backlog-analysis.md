---
schema_version: 1
id: project-todo-backlog-analysis
sprint: null
primary_repo: workitem-conductor
status: draft
executable: true
commits: []
depends_on: []
related: []
blocked_until: null
owner_team: null
external_handoff: false
created_at: 2026-07-17
completed_at: null
---

# Project TODO and Backlog Analysis
**Date:** 2026-07-17
**Scope:** Analyze the `workitem-conductor` repository, with focus on `README.md`, `docs/backlog.md`, `docs/backlog-adjusted.md`, `docs/local-agent-brief.md`, `src/`, `tests/`, and existing TODO/backlog signals. Produce an improved, actionable TODO/backlog document for this repository.

## Background
The repository already contains multiple sources of product and implementation direction: the README, an older backlog, an adjusted consolidated backlog, and a local agent brief. These sources are valuable but overlap, mix current and historical thinking, and vary in granularity. The user wants the project analyzed so the TODO list becomes clearer, better prioritized, and easier to execute through future workitems.

No `.ai/domain-brief.md` was present at planning time, so context was derived from the repository README and existing docs. No sprint was agreed for this work, so `sprint` is intentionally `null` and no sprint-scoped `conductor plans list --sprint` dependency lookup was run.

## Goal
Create a better project TODO list by auditing current documentation and code state, deduplicating overlapping backlog items, identifying completed versus pending work, and producing a prioritized, implementation-oriented TODO/backlog artifact that can drive future executable plans.

## Out of scope
- Implementing backlog items discovered during the analysis.
- Refactoring source code or tests.
- Changing runtime behavior of the `conductor` CLI.
- Making product decisions that require owner input without clearly marking them as open questions.
- Removing historical documentation unless the project owner explicitly approves that cleanup.

## Tasks
1. Inventory all current planning and backlog sources, including `docs/backlog.md`, `docs/backlog-adjusted.md`, `docs/local-agent-brief.md`, README sections that imply pending work, and inline TODO/FIXME comments in `src/` and `tests/`.
2. Compare documented backlog claims against the actual repository state by reviewing relevant modules, CLI commands, tests, and docs to classify each item as done, partially done, pending, obsolete, or unclear.
3. Consolidate duplicate or overlapping ideas into a single normalized backlog structure with stable item names, short descriptions, rationale, estimated scope, dependencies, and suggested priority.
4. Separate the backlog into practical execution bands such as immediate fixes, near-term product milestones, architectural foundations, deferred/low-priority ideas, and open questions requiring human decision.
5. Identify gaps where the code or tests suggest missing TODO items not captured by existing docs, especially around provider behavior, workitem lifecycle, config/runtime layout, validation, inspection, dashboard/workspace flows, and execution safety.
6. Draft an improved TODO/backlog document that is concise enough to maintain but detailed enough for future `execution_plans` to be created from individual items.
7. Update or create the chosen backlog artifact after confirming the intended destination, preferring a single canonical document and preserving links or references to older planning sources as needed.
8. Run documentation-oriented validation by checking the final document for consistency, duplicate items, broken internal references, stale claims, and clear prioritization.

## Acceptance criteria
- A canonical improved TODO/backlog document exists in the repository documentation.
- The backlog clearly distinguishes completed, active/next, deferred, obsolete, and open-question items.
- Existing backlog sources are reconciled rather than blindly copied, with duplicate concepts merged.
- Each actionable item has enough context to become a future implementation plan without re-reading all historical docs.
- Any uncertain product or architecture decisions are explicitly listed as open questions instead of hidden assumptions.
- No source code, tests, or runtime behavior are changed as part of this work.

## Risks
- Existing docs may describe aspirational designs that differ from the current implementation, making classification ambiguous.
- Some TODO items may require owner prioritization because they are product direction decisions rather than purely technical cleanup.
- A single canonical backlog may become too large unless the final structure balances detail with maintainability.
- Inline TODO comments may not reflect current priorities and should be validated before promotion into the canonical backlog.
