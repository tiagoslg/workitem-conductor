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
conductor refine               # optional: AI proposes scope/criteria, asking if needed
#   edit .ai/workitems/<id>/goal.yml — scope, acceptance criteria, stop conditions
conductor approve              # mark the goal approved & ready to execute
conductor status               # show the active workitem
conductor execute              # run the flow end-to-end (dry-run providers for now)
conductor doctor               # check prerequisites and provider CLIs
```

## Refining the goal with AI

`define` is mechanical — it just records the goal. `conductor refine` is the
optional, AI-assisted step that turns a one-line goal into a real contract. The
`refiner` role reads the goal, explores the repo, and **decides whether it needs
to ask you anything** before proposing `scope`, `acceptance_criteria`,
`constraints`, `validation` and `stop_conditions`.

Because providers are stateless one-shot CLIs, the dialogue runs as a re-prompt
loop with a deterministic gate (the same idea as the review gate): each round the
refiner emits either `QUESTIONS:` (the conductor asks you in the terminal and
loops) or `CONTRACT:` (a YAML block written back to `goal.yml`). It never
approves — you still review and run `conductor approve`. The number of question
rounds is bounded by `refine.max_question_rounds` in `repo.yml` (default 5).

`refine` uses the provider bound to the `refiner` role; with no binding (or
`--dry-run`) it makes no proposal. Bind it like any other role:

```yaml
roles:
  refiner: { provider: codex_cli }
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
  qwen_cli:   { type: cli_one_shot, command: qwen, args: ["--approval-mode", "yolo"], prompt_via: arg }
  qwen_api:   { type: api, base_url: https://api.example.com/v1, model: qwen2.5-coder, api_key_env: QWEN_API_KEY }
roles:
  planner:     { provider: codex_cli }
  implementer: { provider: codex_cli }
  reviewer:    { provider: claude_cli }
  refiner:     { provider: qwen_api }
```

Provider types:

- **`cli_one_shot`** — drive a headless coding-agent CLI (Codex, Claude, Qwen
  Code) via stdin or an argument. The CLI manages its own auth. For CLIs that
  gate file edits behind approval (Qwen Code), pass the auto-approve flag in
  `args` for roles that must write — e.g. `args: ["--approval-mode", "yolo"]`
  for an implementer — and a read-only/plan flag (or none) for a reviewer.
- **`api`** — call an OpenAI-compatible `chat/completions` endpoint (OpenAI,
  Qwen, vLLM, LM Studio, a gateway). Requires `base_url`, `model` and
  `api_key_env`; the key is read from that environment variable and **never
  stored**. Uses only the standard library (no extra dependency).
- **`ollama`** — call a native local Ollama server (`/api/chat`). Only `model` is
  required; `base_url` defaults to `http://localhost:11434`. No API key (local,
  unauthenticated). Stdlib only.

```yaml
providers:
  local_qwen: { type: ollama, model: qwen2.5-coder }
roles:
  refiner: { provider: local_qwen }
```

`conductor doctor` shows each binding and whether its command is on PATH (for
`cli_one_shot`), its API-key env var is set (for `api`), or the Ollama server is
up with the model pulled (for `ollama`). The conductor never logs in for you —
CLIs must already be authenticated and API keys must be exported in your
environment.

## Watching work across projects

Each repo keeps its own `.ai/`, so by default you only see a workitem from inside
its repo. To see work across several projects at once, register their roots in a
global **workspace registry** and open the read-only dashboard:

```bash
conductor workspace add ~/projects/service-a          # default workspace
conductor workspace add ~/projects/service-b -w work  # a named workspace
conductor workspace list                              # roots + workitem counts
conductor dashboard                                   # localhost web view
```

`conductor dashboard` starts a small server on `127.0.0.1` (default port 8787;
`--no-open` to skip launching a browser, `-w NAME` to scope to one workspace). It
scans the registered projects and renders every workitem's state, auto-refreshing
every few seconds.

It is **read-only** — it never runs agents or writes anything, only reads the
`state.yml` files the engine produces — and binds to loopback only. The registry
lives under `~/.config/conductor/` and stores paths only (no project state, no
secrets). It records *where* your projects are, nothing about how to reach any
model.

## State model

Each workitem keeps a compact `state.yml` (stage, status, next action,
iterations, open issues, artifacts, history) alongside its `goal.yml`. The
model is deliberately small — the smallest workflow that preserves safety — and
designed to grow toward the execution loop without restructuring.

## Roadmap

The conductor grows along two mostly-independent tracks: **execution** (making
the loop richer and safer) and **visibility/UX** (seeing and steering work
across projects).

### Done

- **MVP 1:** `init` + `define` + `approve` + `status` with an explicit state
  model and artifact layout.
- **MVP 2 — execution loop:** a `flows` loader, the `Provider` interface +
  `DryRunProvider`, a context builder, and the `core` engine that drives the
  flow (select role → build context → call provider → capture artifact → advance
  state → final report). Includes the real `cli_one_shot` provider, the
  role→provider registry read from `repo.yml` (unbound roles fall back to
  dry-run, `execute --dry-run` forces it), and the review/fix back-edge: a
  review-gated step parses `REVIEW: approved` / `changes_requested`; on changes
  the loop returns to the implementer (stage `fixing`) up to `max_fix_iterations`,
  then stops for the human.
- **AI-assisted goal definition:** `conductor refine` — a `refiner` role that
  asks clarifying questions when needed (via a `QUESTIONS:`/`CONTRACT:` gate) and
  writes the goal contract back to `goal.yml`, bounded by
  `refine.max_question_rounds`.
- **`api` provider:** run any role against an OpenAI-compatible HTTP endpoint
  (stdlib-only), alongside the `cli_one_shot` providers.

### Track A — execution

- **`ollama` provider** — native local models (reuses the `api` HTTP path).
- **Context/token strategy** — summarise prior step outputs between steps instead
  of replaying them verbatim (today's context grows with every fix iteration).
- **Semantic stop conditions** — scope change, secrets/prod access,
  reviewer/implementer deadlock (the deterministic caps already exist).
- **Reopen/re-run** a completed workitem (`reopen` / `execute --from <step>`).
- **`cli_pty` provider** *(on-demand)* — drive interactive-only CLIs via a
  pseudo-terminal; the most brittle provider, built only when a needed CLI lacks
  a headless mode.
- **Sessions/sandbox** — git worktrees, generated docker-compose, dynamic ports,
  smoke tests. The workitem (memory/state) is already separated from the session
  (runtime), so this is additive.

### Track B — visibility / UX

- **B1 — workspace registry** (CLI, global `~/.config/conductor/`): register and
  group project roots — the spine of "what to show."
- **B2 — read-only dashboard** (`conductor dashboard`): an on-demand localhost
  web view that scans registered workspaces and renders every workitem's state.
  Pure read, low risk — good for sharing the picture with a team.
- **B3 — interactive layer** *(later)*: a config cascade (global → workspace →
  repo `.ai/` overrides) for per-step model defaults, plus triggering/approving
  runs from the UI. The UI configures *env-var names*, never stored keys, and
  stays localhost-only until auth is designed.

### Smaller tweaks

- `init` infers `name` from the directory instead of `TODO`.
- Refiner prompt polish (emit only the marker block; tighter `scope.include`).

## Design principle

A step exists only if it reduces risk, saves human attention, improves quality,
or creates evidence needed for a decision. If a step only makes the process look
more complete, it should not exist.
