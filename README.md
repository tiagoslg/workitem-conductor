# workitem-conductor

**Define the goal. Let agents do the loop. Review the result.**

`workitem-conductor` is a local **conductor** for AI-assisted development
workflows. It is *not* another coding agent — it coordinates the coding agents
and model providers you already use (Codex CLI, Claude CLI, Ollama, API models),
running the implement → review → fix loop so you don't have to copy prompts and
handoffs between them by hand.

You stay responsible for two things: **defining/approving the goal** and
**validating the final result**. The conductor owns the operational loop in
between and stops to ask you only when it's blocked or done.

## Vocabulary

| term | meaning |
| --- | --- |
| **workitem** | unit of intent, state, evidence and final report |
| **session** | execution/sandbox environment for a workitem *(future)* |
| **flow** | ordered process, e.g. plan → implement → review → fix → validate |
| **role** | a responsibility in a flow: planner, implementer, reviewer, … |
| **provider** | an execution backend: Codex CLI, Claude CLI, Ollama, an API model |

A role is **not** the same as a provider — `reviewer` can run on Claude today
and another model tomorrow without changing the flow.

## Install

```bash
pip install -e ".[dev]"   # from this repo, for development
```

This puts the `conductor` command on your PATH.

## Quickstart

Run from inside the repository you want to work on:

```bash
cd ~/projects/my-service
conductor init                 # scaffold .ai/ (config, flow, role prompts)
conductor define "fix the policy discovery bug"
#   edit .ai/workitems/<id>/goal.yml — scope, acceptance criteria, stop conditions
conductor approve              # mark the goal approved & ready to execute
conductor status               # show the active workitem
conductor execute              # run the flow end-to-end (dry-run providers for now)
conductor doctor               # check prerequisites and provider CLIs
```

## What `init` writes

Versionable configuration is separated from runtime artifacts:

```
.ai/
  repo.yml                 # repo config; role → provider mapping (versioned)
  instructions.md          # repo-specific guidance (versioned)
  flows/simple-change.yml  # the default flow (versioned)
  roles/planner.md         # provider-neutral role prompts (versioned)
  roles/implementer.md
  roles/reviewer.md
  .gitignore               # ignores the runtime dirs below

  workitems/<id>/          # runtime: goal.yml, state.yml, outputs/, reviews/
  active_workitem.txt      # pointer to the active workitem
```

`init` is idempotent — it never overwrites files you've edited.

## Configuring providers

Bind roles to backends in `.ai/repo.yml`. A provider is declared once and
referenced by name, so the same flow runs on different backends by editing
config alone. Unbound roles run in dry-run.

```yaml
providers:
  codex_cli:  { type: cli_one_shot, command: codex, args: ["exec"], prompt_via: arg }
  claude_cli: { type: cli_one_shot, command: claude, args: ["-p"],   prompt_via: arg }
roles:
  planner:     { provider: codex_cli }
  implementer: { provider: codex_cli }
  reviewer:    { provider: claude_cli }
```

`conductor doctor` shows each binding and whether its command is on PATH.
The conductor never logs in for you — the CLI must already be authenticated.

## State model

Each workitem keeps a compact `state.yml` (stage, status, next action,
iterations, open issues, artifacts, history) alongside its `goal.yml`. The
model is deliberately small — the smallest workflow that preserves safety — and
designed to grow toward the execution loop without restructuring.

## Roadmap

- **MVP 1 (done):** `init` + `define` + `approve` + `status` with an explicit
  state model and artifact layout.
- **MVP 2 — execution loop:**
  - *slice 1 (done):* `flows` loader, a `Provider` interface with a
    `DryRunProvider`, a context builder, and a `core` engine that drives the
    flow — selecting each role, building its context, capturing output as
    artifacts, advancing state, and writing a final report. `conductor execute`
    runs this loop end-to-end without calling a real model yet.
  - *slice 2a (done):* a real `cli_one_shot` provider (drives a headless CLI
    via stdin/arg) and a role→provider registry read from `repo.yml`. Roles
    with no binding fall back to dry-run; `execute --dry-run` forces dry-run.
    The conductor does **not** own provider authentication — CLIs are
    configured (and logged in) outside it.
  - *slice 2b (next):* the review/fix back-edge and stop conditions
    (max iterations, repeated blocker, scope/secret/prod guards). Future
    provider types: `cli_pty`, `api`, `ollama`.
- **MVP 3 — sessions/sandbox:** git worktrees, generated docker-compose,
  dynamic ports and smoke tests. The workitem (memory/state) is already
  separated from the session (runtime), so this is additive.

## Design principle

A step exists only if it reduces risk, saves human attention, improves quality,
or creates evidence needed for a decision. If a step only makes the process look
more complete, it should not exist.
