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
| **flow** | ordered process, e.g. plan → implement → review → fix → validate |
| **role** | a responsibility in a flow: planner, implementer, reviewer, … |
| **provider** | an execution backend: Codex CLI, Claude CLI, Ollama, an API model |

A role is **not** the same as a provider — `reviewer` can run on Claude today
and another model tomorrow without changing the flow.

## Install

```bash
pip install -e ".[dev]"   # from this repo, in a virtualenv
```

Or install globally with [pipx](https://pipx.pypa.io/) so `conductor` is on
your PATH without activating a virtualenv:

```bash
pipx install -e /path/to/workitem-conductor
```

This puts the `conductor` command on your PATH.

## Quickstart

Run from inside the repository you want to work on:

```bash
cd ~/projects/my-service
conductor init                      # scaffold .ai/ (config, flow, role prompts)
conductor define "fix the policy discovery bug"
conductor refine                    # optional: AI proposes scope/criteria, asking if needed
#   edit .ai/workitems/<id>/goal.yml — scope, acceptance criteria, stop conditions
conductor approve                   # mark the goal approved & ready to execute
conductor status                    # show the active workitem
conductor execute                   # run the flow end-to-end
conductor execute --stream          # same, but stream provider output live
conductor accept                    # commit the result; see "Git workflow" below
conductor doctor                    # check prerequisites and provider CLIs
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
  worktrees/<id>/          # git worktree checkout (active during execute)
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
  implementer: { provider: qwen_cli }
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

## Git workflow

`conductor execute` creates an isolated **git worktree** at
`.ai/worktrees/<id>/` on a branch called `conductor/<id>`. All agent edits
happen inside that worktree and never touch your working tree.

When you're happy with the result, `conductor accept` brings the changes in:

```bash
conductor accept          # commit + merge worktree, remove it
conductor accept --push   # same, then push the feature branch
```

Accept does, in order:
1. `git add -A && git commit` inside the worktree (conventional commit message
   derived from `feature_branch`; see below).
2. Merge `conductor/<id>` into `target_branch` (or current HEAD if unset).
3. Create the feature branch pointer (`git branch -f feat/... conductor/<id>`).
4. Remove the worktree.
5. If `--push`: push the feature branch to origin so you can open a PR.

### Feature branch from the planner

The planner can emit a `BRANCH:` directive on its own line:

```
BRANCH: feat/fix-policy-discovery
```

The conductor saves this name in state. At `accept` time it creates a local
branch pointing to the committed tip — ready for a PR to `main` — while the
merge itself goes into `target_branch` (typically `develop`).

Commit messages follow the [Conventional Commits](https://www.conventionalcommits.org/)
format, derived automatically from the branch prefix:

| feature branch prefix | commit prefix |
| --- | --- |
| `feat/` | `feat:` |
| `fix/` | `fix:` |
| `refactor/` | `refactor:` |
| … | … |

Example: `feat: fix policy discovery bug\n\nWorkitem: wi-20240615-abc123`

### Branch strategy config

By default worktrees branch from the current HEAD and merge back into it.
To lock the strategy for a repo, set in `.ai/repo.yml`:

```yaml
source_branch: main     # worktrees are always created from this branch
target_branch: develop  # conductor accept always merges into this branch
```

With this config, `conductor accept --push` produces a feature branch ready
for a PR to `main`, regardless of what branch you happen to have checked out.

## Rewinding with `conductor reopen`

If the result needs revision, reopen the workitem instead of starting over:

```bash
conductor reopen "the last migration was wrong — column types don't match"
```

`reopen` resets `step_index` and writes a `reopen.md` alongside the goal. The
context builder injects it as a `## Reopen reason` section so the planner treats
the rerun as a directed revision of the prior plan. The worktree and feature
branch are left intact — reopening is continuation, not discard.

### Skipping the planner with `--from`

For small corrections that don't need a new plan, restart from a later step:

```bash
conductor reopen "fix the path in the /emails endpoint" --from implementer
```

This skips the planner entirely. The implementer receives:
- The reopen reason (what to fix)
- The prior reviewer and validator output (what they flagged as wrong)
- The worktree as-is (all previous work preserved)

Use `--from` when you know exactly what to fix and re-planning would just
re-evaluate work that was already correct. Use a full reopen (no `--from`) when
the plan itself needs to change.

## Watching execution

During `execute`, each step shows a spinner with the active role and which
provider is executing it:

```
  planner (codex) thinking... 12s
```

For slow models that can loop silently, add `--stream` to see raw provider output
in real time:

```bash
conductor execute --stream
```

Prompt files for each step are written to `.ai/workitems/<id>/outputs/` **before**
the provider runs, so you can inspect what was sent to the model while it's
thinking.

## Cross-project workitems and workspace execution

### Defining cross-project workitems

```bash
conductor workspace add ~/projects/service-a          # default workspace
conductor workspace add ~/projects/service-b -w work  # a named workspace
conductor workspace list
conductor define "migrate auth tokens across all services" -w default
conductor refine -w default
conductor approve -w default
conductor status -w default
```

Cross-project workitems live under `~/.config/conductor/workspaces/<name>/` and
receive context from all repos in the workspace (instructions + paths) during
`refine`, so the refiner can reason across projects.

### Executing cross-project workitems

```bash
conductor execute -w default          # two-phase: planner once, then per-repo
conductor execute -w default --stream # same with live output
conductor accept  -w default          # commit + merge in every project repo
conductor accept  -w default --push   # same, then push each feature branch
conductor reopen  "reason" -w default # reopen and re-run across all repos
```

The workspace flow runs in two phases:
1. **Planner** once, with the combined cross-project context.
2. **Implementer + reviewer** independently per project, each in its own
   worktree.

### Read-only dashboard

```bash
conductor dashboard           # localhost web view of all registered projects
conductor dashboard -w work   # scoped to one workspace
```

`conductor dashboard` starts a small server on `127.0.0.1` (default port 8787;
`--no-open` to skip launching a browser). It scans the registered projects and
renders every workitem's state, auto-refreshing every few seconds.

It is **read-only** — it never runs agents or writes anything, only reads the
`state.yml` files the engine produces — and binds to loopback only. The registry
lives under `~/.config/conductor/` and stores paths only (no project state, no
secrets).

## State model

Each workitem keeps a compact `state.yml` (stage, status, next action,
iterations, open issues, artifacts, history, feature branch) alongside its
`goal.yml`. The model is deliberately small — the smallest workflow that
preserves safety — and designed to grow toward the execution loop without
restructuring.

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
  (stdlib-only), alongside the `cli_one_shot` providers. The refiner gate is
  tolerant of models that follow it loosely (markers inferred; each round's raw
  output captured under the workitem for diagnosis).
- **`ollama` provider** — native local models via Ollama (`/api/chat`); no API
  key required; `base_url` defaults to `http://localhost:11434`.
- **Context/token strategy** — prior step outputs are deduped by role (most
  recent per role only) and capped; fix-iteration header added so agents know
  they are in a fix loop.
- **Refiner YAML robustness** — prompt rule to avoid TypeScript-like syntax in
  YAML values; `_preprocess_yaml` fallback that quotes problematic values before
  retrying `yaml.safe_load`; `_contract_list_items_are_strings` guard against
  silent mapping mis-parses.
- **Visibility B1–B2:** a global **workspace registry** (`conductor workspace
  add/list/remove`, stored under `~/.config/conductor/`) and a **read-only
  dashboard** (`conductor dashboard`) — an on-demand localhost web view that
  scans the registered projects and renders every workitem's state. Pure read,
  loopback-only.
- **Cross-project workitems** — `conductor define/refine/approve/status -w
  <workspace>` creates workitems that live at the workspace level (under
  `~/.config/conductor/workspaces/<name>/`). The refiner receives context from
  all repos in the workspace (instructions + paths) so it can reason about
  cross-project bugs and changes.
- **`conductor execute -w <workspace>`** — two-phase workspace execution: planner
  once with cross-project context, then implementer + reviewer independently per
  project in isolated git worktrees.
- **Git worktree isolation** — `execute` creates `.ai/worktrees/<id>/` on
  `conductor/<id>` so agent edits never touch the working tree.
- **`conductor accept`** — commit the worktree (`git add -A && git commit`),
  merge into `target_branch`, create the feature branch pointer, remove the
  worktree. `--push` pushes the feature branch after merging.
- **Branch strategy config** — `source_branch` / `target_branch` in `repo.yml`
  so worktrees always branch from (e.g.) `main` and `accept` always merges into
  `develop`, regardless of current HEAD.
- **Feature branch from planner** — planner emits `BRANCH: feat/...`; conductor
  saves it in state and creates a local branch at `accept` time. Commit messages
  follow the Conventional Commits format derived from the branch prefix.
- **`conductor reopen "<reason>"`** — resets `step_index` and injects
  `reopen.md` as planner context. `--from <role>` restarts from a specific step.
  Worktree and feature branch are left intact.
- **Live progress during `execute`** — spinner shows role + provider name while
  each step runs; `--stream` streams raw provider output live instead.
- **Prompt files before provider call** — each step's prompt is written to disk
  before the provider runs, so you can inspect it while the model is thinking.

### Track A — execution

- **Semantic stop conditions** — scope change, secrets/prod access,
  reviewer/implementer deadlock (the deterministic caps already exist).
- **`cli_pty` provider** *(on-demand)* — drive interactive-only CLIs via a
  pseudo-terminal; the most brittle provider, built only when a needed CLI lacks
  a headless mode.

### Track B — visibility / UX

B1 (workspace registry), B2 (read-only dashboard), and cross-project workitems
have shipped — see *Done*.

- **B3 — interactive layer** *(next on this track)*: a config cascade (global →
  workspace → repo `.ai/` overrides) for per-step model defaults, plus
  triggering/approving runs from the UI. The UI configures *env-var names*, never
  stored keys, and stays localhost-only until auth is designed.

### Backlog

- **CLI tab completion** — re-enable Typer's shell completion (`add_completion=True`)
  for commands and flags; add dynamic completion for workitem IDs and workspace names.
- **Better terminal input in `refine`** — `typer.prompt()` doesn't support
  readline (arrow keys, `Ctrl+←`, history). Fix: activate `readline` stdlib before
  the question loop, or use `prompt_toolkit` for a richer experience.
- **AI commit messages** — call a lightweight provider with `git diff --staged`
  to generate a richer conventional commit message. Keep the mechanical fallback
  if the provider fails or isn't configured.

## Design principle

A step exists only if it reduces risk, saves human attention, improves quality,
or creates evidence needed for a decision. If a step only makes the process look
more complete, it should not exist.
