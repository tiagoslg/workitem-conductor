# workitem-conductor

A local control-plane and audit layer for `execution_plans/*.md` files —
the plan documents used to hand cross-repo work to coding agents (via
[OpenCode](https://opencode.ai) or any other CLI-based agent tool).

`conductor` never calls a model and never executes anything. It scans,
validates, tracks status, and reports on plan files that already exist on
disk — the plans themselves are authored by a conversation (with OpenCode's
`plan-writer` agent, or by hand) and executed by another conversation (with
OpenCode's `/implement-plan`, or whatever tool you use day to day).

See [`docs/audit-layer-pivot.md`](docs/audit-layer-pivot.md) for the full
design rationale and history — this project pivoted from an earlier design
(a coding-role orchestrator that called Codex/Claude/Ollama CLIs directly)
once that problem was better solved by living inside OpenCode's own
multi-agent setup day to day. What's left is the piece OpenCode doesn't do
on its own: tracking a plan's identity, its dependencies on other plans
(possibly in other repos), and its status, across a conversation that
authored it and a separate conversation that executed it.

## The convention: `execution_plans/*.md`

A plan is a markdown file at `.ai/execution_plans/<date>_<slug>.md` inside a
repo, with a small YAML frontmatter block followed by free-form prose:

```yaml
---
schema_version: 1
id: fix-duplicate-quote-properties
sprint: null
primary_repo: my-service
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

# Fix duplicated quote properties

**Date:** 2026-07-17
**Scope:** my-service

## Background
...

## Goal
...

## Out of scope
...

## Tasks
1. ...

## Acceptance criteria
- ...

## Risks
- ...
```

The frontmatter is the only machine-readable part — it's what `conductor`
parses. The body stays free prose, deliberately: forcing a plan's tasks and
reasoning into rigid structured fields loses information an implementer
needs, for no analytical benefit.

`status` values: `draft` (still being written) → `ready` (approved, safe to
execute) → `in_progress` → `done` (or `blocked`/`canceled`). Only a human,
or `conductor plans mark`, moves a plan out of `draft` — nothing in this
tool writes `ready` or `done` on its own.

## Install

```bash
pip install -e .
```

Exposes the `conductor` command.

## Commands

### `conductor workspace`

A global registry of project roots (`~/.config/conductor/workspaces.yml`),
grouped into named workspaces — the set of repos `conductor plans` scans.

```bash
conductor workspace add /path/to/repo [-w NAME]
conductor workspace remove /path/to/repo [-w NAME]
conductor workspace list
```

### `conductor plans`

```bash
conductor plans list [--sprint NAME] [--repo NAME] [-w WORKSPACE]
conductor plans ready [-w WORKSPACE]           # ready + every dependency already done
conductor plans lint [PLAN_ID] [-w WORKSPACE]  # dependency/cycle/evidence checks
conductor plans mark PLAN_ID ready
conductor plans mark PLAN_ID done --commit SHA
conductor plans table [--sprint NAME] [-w WORKSPACE]  # markdown table for a PR
```

`plans list` scans every repo in the registry (plus the current directory,
if it isn't already registered) for `.ai/execution_plans/*.md`, parses each
file's frontmatter, and reports it — nothing is scanned recursively into
source code, and nothing is ever written except by `plans mark`, which edits
only the frontmatter block of the target plan file in place. There is no
separate database; the plan file is always the source of truth.

`plans lint` is the most load-bearing command: it checks that every
`depends_on`/`related` reference resolves to a real plan, that there are no
dependency cycles, that a `done` plan has at least one commit recorded, that
a plan isn't marked `done` while a dependency isn't, and — for any plan
marked `external_handoff: true` (handed to a team without access to the
repos it depends on) — that every dependency it names also has an inline
summary in the plan's own body, not just a bare reference.

## What this project deliberately does not do

- **No LLM calls, ever.** Plan authoring happens in a separate tool/
  conversation (OpenCode's `plan-writer` agent via its `/create-plan`
  command); plan execution happens in another (OpenCode's
  `/implement-plan`, coordinating `implementer`/`reviewer`/`tester`/
  `committer` subagents). `conductor` only reads what those conversations
  produced.
- **No workitem lifecycle, no worktrees, no execution engine.** An earlier
  version of this project had all of that — see
  `docs/audit-layer-pivot.md` §1 for why it was cut rather than kept
  around "just in case."
- **No live dependency on OpenCode's own session database.** Correlating a
  plan to the OpenCode sessions that executed it (cost, model, tokens) is a
  planned addition (`conductor plans sync`, not yet built) that will snapshot
  that data into `conductor`'s own storage rather than reading it live —
  see the design doc's §4.3/§7 for why.

## Development

```bash
pip install -e ".[dev]"
pytest
```
