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
#   edit goal.yml (path shown by `define`/`conductor doctor`) — scope, acceptance criteria, stop conditions
conductor approve                   # mark the goal approved & ready to execute
conductor status                    # show the active workitem
conductor execute                   # run the flow end-to-end
conductor execute --stream          # same, but stream provider output live
conductor inspect                   # state + run history/metrics; see below
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

`.ai/` holds only versionable configuration — it is never gitignored, so it
can be committed alongside the code it configures:

```
.ai/
  repo.yml                 # repo config; role → provider mapping (versioned)
  instructions.md          # repo-specific guidance (versioned)
  flows/simple-change.yml  # the default flow (versioned)
  flows/phased-change.yml  # per-phase implement+review, single final verify
  strategies/              # named flow+role+context+budget bundles (see "Strategies")
    simple-change.yml
    bugfix.yml
    context-heavy-change.yml
    phased-documentation.yml
  roles/planner.md         # provider-neutral role prompts (versioned)
  roles/implementer.md
  roles/reviewer.md
  roles/verifier.md
  roles/refiner.md
```

Runtime state (workitems, worktrees, the active-workitem pointer) never lives
inside the repo. It's created lazily under a central, per-project data
directory the conductor owns:

```
~/.local/share/conductor/projects/<project-id>/
  workitems/<id>/          # goal.yml, state.yml, outputs/, reviews/
  worktrees/<id>/          # git worktree checkout (active during execute)
  active_workitem.txt      # pointer to the active workitem
```

`<project-id>` is derived from the repo's resolved path and is stable across
invocations from different cwd/subdirectories; `conductor doctor` prints the
exact resolved path for the current repo. This directory (and the machine-wide
`~/.config/conductor/` and `~/.cache/conductor/`) resolve via
`CONDUCTOR_DATA_HOME`/`CONDUCTOR_CONFIG_HOME`/`CONDUCTOR_CACHE_HOME` (falling
back to the matching `XDG_*_HOME`, then the POSIX defaults) if you need to
relocate them.

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

## Strategies

A **strategy** is a named, reusable bundle — which flow to run, plus optional
overlays on top of `repo.yml`'s role bindings, context budget, and fix-loop
cap — so different kinds of workitems don't all have to be treated
identically. It overlays `repo.yml`, it doesn't replace it: provider
*definitions* (`type`/`command`/`args`) always live in `repo.yml`'s
`providers:` dict; a strategy can only change which provider a role is
*bound* to, plus context/budget values.

```yaml
# .ai/strategies/context-heavy-change.yml
name: context-heavy-change
flow: simple-change
roles:
  implementer: { provider: qwen_cli }   # overrides repo.yml's binding for this role only
context:
  max_prompt_chars: 96000
  include_raw_outputs: true
max_fix_iterations: 3
```

Four strategies are scaffolded by `init`: `simple-change` (the default —
identical behavior to running with no strategy at all), `bugfix` (tighter
`max_fix_iterations`), `context-heavy-change` (bigger prompt budget, raw
outputs instead of curated memory), and `phased-documentation` (currently a
placeholder — real phase-by-phase execution is a separate, later piece of
work; for now it behaves like `simple-change`).

A workitem's strategy is picked by a small rule-based selector, run once at
`conductor define` (before `refine`, so it almost always lands on
`simple-change` since `acceptance_criteria` is still empty) and again at
`conductor approve` — the evaluation that actually matters, once `refine` (or
a hand-edit) has filled in the goal contract:

```
if acceptance_criteria mentions "docs"/"documentation" -> phased-documentation
else                                                   -> simple-change
```

Pin one explicitly instead of trusting the selector:

```bash
conductor define "fix the null check" --strategy bugfix
conductor approve --strategy context-heavy-change
```

`--strategy` **locks** the choice (`state.strategy_locked`) so `approve`'s
automatic reselection won't clobber a human's explicit pick. The active
strategy shows up in `conductor inspect`/`conductor status`, and every run
records both `strategy` and a short content hash (`strategy_hash`) in
`run.yml`, so you can tell later which version of a strategy was in effect
even if the file's been edited since.

Two rules from the original design aren't reachable yet, on purpose rather
than silently: `target_projects > 1 -> cross-project-change` (workspace
workitems don't call the selector — `conductor execute -w` still hardcodes
its own flow) and `reopen_count >= 2 -> context-heavy-change` (`conductor
reopen` goes straight back to ready-to-execute without an `approve` step, so
there's no point to reselect from yet). `--strategy` is rejected outright
with `-w` (`define -w ... --strategy ...` / `approve -w ... --strategy ...`
both exit with an error) rather than silently accepted and ignored.

## Structured role output

The **planner**'s output is a fenced YAML block up front (`branch`/`phases`/
`risk_level` — see "Feature branch from the planner" below), followed by the
plan in prose. `phases` is always recorded (in `state.yml`/`run.yml`), but
only actually *drives* phase-by-phase execution for flows that opt in — see
[Phased execution](#phased-execution).

The **reviewer** still gates the fix loop with a single `REVIEW:
approved|changes_requested` line, unchanged — but can optionally follow it
with a fenced YAML block:

```yaml
confidence: 0.8
blocking_issues:
  - the null check is missing on the new endpoint
non_blocking_issues:
  - could rename this variable for clarity
suggested_next_role: implementer
```

This is purely informational: it's recorded in `run.yml` and shown in
`conductor inspect`, but `suggested_next_role` doesn't currently override the
flow's own loop-back target (`on_changes`).

The **verifier** role runs after the reviewer approves, and is a real gate:

```yaml
tests_run: true
tests_passed: true
acceptance_criteria_met: true
notes:
  - ran the full suite, all green
```

preceded by `VERIFY: passed|failed`. A `failed` verdict loops back to the
implementer exactly like the reviewer's `changes_requested` — sharing the
same `max_fix_iterations` budget, not a separate counter. An absent `VERIFY:`
line (e.g. a dry-run provider) is treated as `passed`, same permissive
posture as an absent `REVIEW:` line. The verifier is single-repo only for
now — `conductor execute -w` doesn't have a verifier step in its flow yet.

## Phased execution

`simple-change` always runs one implementer → reviewer → verifier pass over
the whole plan, regardless of how many phases the planner identified —
`phases` is recorded but doesn't change control flow for that flow. A flow
opts into real phase-by-phase execution with a `phase_flow`:

```yaml
# .ai/flows/phased-change.yml
name: phased-change
steps:
  - role: planner
    stage: planning
  - role: verifier
    stage: verifying
    gate: verify
    on_changes: implementer
phase_flow:               # walked once per planner phase
  - role: implementer
    stage: implementing
  - role: reviewer
    stage: reviewing
    gate: review
    on_changes: implementer
max_fix_iterations: 3
```

The `phased-documentation` strategy uses this flow — pick it explicitly
(`conductor define ... --strategy phased-documentation`) or let the docs
rule in the selector pick it. For each phase, the implementer/reviewer run
in sequence with a `## Current phase` section in their prompt (name, goal,
files likely touched) instead of the whole plan; a `changes_requested` loops
back within that phase only. Every phase's fix loop shares the same
`fix_iterations`/`max_fix_iterations` budget — one counter across the whole
run, not one per phase. The **verifier still runs once**, after every phase
has completed, not per phase; if it fails, the conductor redoes just the
*last* phase (its implementer/reviewer) rather than restarting the whole
plan, since the verifier's `on_changes: implementer` target only exists
inside `phase_flow`. If the planner emits no phases at all, `phase_flow`
still runs once against an implicit single phase rather than doing nothing.

`conductor inspect` shows `phase N/M` alongside the usual stage/status once
a phased run has started; `run.yml`/`conductor execute`'s live output tag
each `phase_flow` step with its phase name, the same way workspace runs tag
steps with a project name.

## Git workflow

`conductor execute` creates an isolated **git worktree** under the central
data directory (`~/.local/share/conductor/projects/<project-id>/worktrees/<id>/`,
shown by `conductor doctor`) on a branch called `conductor/<id>`. All agent
edits happen inside that worktree and never touch your working tree.

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

The planner's output is a fenced YAML block (see "Structured role output"
below) with a `branch` field:

```yaml
branch: feat/fix-policy-discovery
phases:
  - name: locate-the-bug
    goal: find where policy discovery reads the wrong config key
risk_level: low
```

The conductor saves `branch` in state. At `accept` time it creates a local
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

Prompt files for each step are written to the workitem's `outputs/` directory
(under the central data dir — `conductor execute` prints its path) **before**
the provider runs, so you can inspect what was sent to the model while it's
thinking.

## Runs, metrics and `conductor inspect`

Every `conductor execute` call is a **run**. Each run writes a manifest under
the workitem's directory:

```
runs/run-001/
  run.yml       # per-step provider/duration/verdict, started/finished, status
  metrics.yml   # aggregated: context size, git diff stats, fix/reopen counts, providers used
```

`run.yml` references the existing `outputs/NN-role.*.md` files rather than
duplicating them — a run is a record of *what happened*, not a copy of the
artifacts. `metrics.yml`'s git stats (`files_changed`/`insertions`/`deletions`)
are best-effort: `None` when the execution directory isn't a git repo.

Inspect a workitem's goal/state plus its run history:

```bash
conductor inspect                # active workitem: state + latest run
conductor inspect --active       # same as above, explicit form
conductor inspect <id>           # a specific workitem
conductor inspect --runs         # every run, not just the latest
conductor inspect --context      # + per-step prompt/output char counts
```

It also shows a live `git diff --stat` of the worktree, if one still exists
(reopened/accepted workitems won't have one).

`conductor execute -w <workspace>` writes run/metrics manifests too — see
[Cross-project workitems and workspace execution](#cross-project-workitems-and-workspace-execution).

## Memory and curated context

Resending every prior step's raw output on every reopen grows the prompt
without bound. Instead, a `summarizer` role curates `memory.yml` — the
workitem's *current* state in its own words — and later prompts read that
instead:

```
memory.yml               # current_summary, open_issues, decisions, resolved_issues
context/current_summary.md   # plain-text copy of current_summary, for humans
```

The summarizer runs automatically (bind it in `repo.yml` like any other role;
unbound = silent no-op, not an error) after a run finishes, stops/blocks,
loops back for a fix, or is reopened — each time reading the goal, prior
memory, the steps that just ran, and the working-tree diff, then emitting one
`SUMMARY:` block (same "one marker" convention as the reviewer's `REVIEW:`
verdict and the refiner's `CONTRACT:`).

By default, `build_context()` assembles each prompt from curated sources —
memory, a working-tree diff, and just the latest reviewer output — instead of
every role's raw prior output. Control this in `repo.yml`:

```yaml
context:
  max_prompt_chars: 64000     # optional sections trim from the tail first
  include_raw_outputs: false  # opt-in — turn on if you don't yet trust the summarizer
  include_memory: true
  include_last_diff: true
  include_last_review: true
```

`conductor reopen` also triggers the summarizer immediately (wrapped so a
failing/unbound summarizer never blocks the reopen itself — resetting state
has to stay reliable). `conductor inspect` shows the current memory alongside
run history.

`conductor execute -w <workspace>` now calls the summarizer too (once at the
end of the run, not per-project), so `memory.yml` gets populated — but
workspace project prompts (`build_workspace_project_context`) don't consume
that memory yet the way single-repo `build_context()` does, and
`conductor reopen -w` still doesn't trigger summarization. Both remain
fast-follows.

## Safety stop conditions

Any role (planner, implementer, reviewer) can hand a workitem back to a human
instead of improvising, by emitting a `STOP:` marker as the first line of its
response — same one-marker convention as `REVIEW:`/`SUMMARY:`:

```
STOP: scope_change | secrets_access | dangerous_command | production_access
<why, in plain text>
- <optional evidence bullet>
- <optional evidence bullet>
```

The engine checks every step's output for this marker (not just review-gated
ones) and, if present, stops immediately — before any `REVIEW:` verdict on
the same output is even considered. The workitem's `stop_reason` (type,
message, evidence) is recorded in both `state.yml` and the run's `run.yml`,
and shown by `conductor inspect` and the final report.

`status` reflects *why* it stopped: `blocked` for a technical/provider
failure, `needs_human` for everything semantic (a `STOP:` marker, the global
step cap, or the fix loop being exhausted) — a decision or risk a human needs
to weigh in on, not a bug.

Deterministic detection of a stuck fix loop (repeated near-identical
review/implementer output) is not implemented yet — `max_fix_iterations`
remains the only backstop for that case.

`conductor execute -w <workspace>` checks for the same marker on the
planner's output and on every project's implementer/reviewer output. Unlike a
plain provider failure (which only skips that one project and moves on to
the next), a `STOP:` marker halts the *entire* workspace run immediately —
projects not yet reached (or not yet started, if the planner itself raised
it) are never touched.

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

A workspace's curated config (`config.yml`, `instructions.md`, `roles/`,
`flows/`) lives under `~/.config/conductor/workspaces/<name>/`; its runtime
state (workitems, active-workitem pointer) lives under
`~/.local/share/conductor/workspaces/<name>/` — the same config/data split
`.ai/` has for single-repo projects. The refiner receives context from all
repos in the workspace (instructions + paths) during `refine`, so it can
reason across projects.

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

Each `execute -w` call writes one `run.yml`/`metrics.yml` for the whole
workspace run (planner step plus every project's steps — `conductor inspect
-w <ws>` shows the full history) and calls the `summarizer` role once at the
end to update the workitem's shared `memory.yml`, same as the single-repo
engine.

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
  <workspace>` creates workitems at the workspace level: config under
  `~/.config/conductor/workspaces/<name>/`, runtime state under
  `~/.local/share/conductor/workspaces/<name>/`. The refiner receives context
  from all repos in the workspace (instructions + paths) so it can reason
  about cross-project bugs and changes.
- **`conductor execute -w <workspace>`** — two-phase workspace execution: planner
  once with cross-project context, then implementer + reviewer independently per
  project in isolated git worktrees.
- **Git worktree isolation** — `execute` creates a worktree under the central
  data directory on `conductor/<id>` so agent edits never touch the working tree.
- **`conductor accept`** — commit the worktree (`git add -A && git commit`),
  merge into `target_branch`, create the feature branch pointer, remove the
  worktree. `--push` pushes the feature branch after merging.
- **Branch strategy config** — `source_branch` / `target_branch` in `repo.yml`
  so worktrees always branch from (e.g.) `main` and `accept` always merges into
  `develop`, regardless of current HEAD.
- **Feature branch from planner** — planner emits a `branch` field in its
  structured YAML output; conductor saves it in state and creates a local
  branch at `accept` time. Commit messages follow the Conventional Commits
  format derived from the branch prefix.
- **`conductor reopen "<reason>"`** — resets `step_index` and injects
  `reopen.md` as planner context. `--from <role>` restarts from a specific step.
  Worktree and feature branch are left intact.
- **Live progress during `execute`** — spinner shows role + provider name while
  each step runs; `--stream` streams raw provider output live instead.
- **Prompt files before provider call** — each step's prompt is written to disk
  before the provider runs, so you can inspect it while the model is thinking.
- **Central runtime storage** — workitems, worktrees and the active-workitem
  pointer moved out of the git-tracked `.ai/` directory into a per-project
  directory under `~/.local/share/conductor/` (`conductor doctor` shows the
  exact path). `.ai/` now holds only versionable config and is no longer
  gitignored by `init`. Named workspaces got the same config/data split —
  curated config stays under `~/.config/conductor/workspaces/<name>/`, runtime
  state moved to `~/.local/share/conductor/workspaces/<name>/`. First step
  toward treating workitems as a first-class concept the conductor owns, ahead
  of runs/metrics/memory work.
- **Runs and metrics** — every `execute` writes `runs/<id>/run.yml` (per-step
  provider, duration, char counts, verdict) and `runs/<id>/metrics.yml`
  (aggregated context size, git diff stats, fix/reopen counts, providers
  used), plus `conductor inspect` to view them alongside goal/state.
- **Memory, summarizer role, curated context** — a `summarizer` role curates
  `memory.yml`/`context/current_summary.md` after each run finish/stop/loop-back
  and `reopen`; `build_context()` now assembles prompts from that curated
  memory (plus a working-tree diff and the latest reviewer output) instead of
  every role's raw prior output by default, under an explicit
  `context.max_prompt_chars` budget. Raw-output inclusion stays available as
  an opt-in for repos not yet trusting their summarizer.
- **Safety stop conditions** — any role can emit a `STOP:` marker (scope
  change, secrets access, dangerous command, production access) as the first
  line of its response, checked on every step and taking priority over a
  `REVIEW:` verdict on the same output. The structured reason (type/message/
  evidence) is recorded in `state.yml` and `run.yml` and shown by `conductor
  inspect` and the final report; `status` is `blocked` for a technical/
  provider failure and `needs_human` for everything semantic. Deterministic
  stuck-loop detection is not implemented yet — `max_fix_iterations` remains
  the only backstop for that.
- **`WorkspaceEngine` fast-follows** — runs/metrics recording, the
  `summarizer` role, and `STOP:` detection now all work for
  `conductor execute -w <workspace>` too, not just single-repo `execute`. One
  `run.yml` covers the whole workspace run (planner + every project's steps,
  distinguished by `StepRecord.project_name`); the summarizer is called once
  at the end rather than per project. A `STOP:` marker from any project's
  role halts the *entire* workspace run immediately — unlike a plain
  provider failure, which only skips that project and moves on.
- **Strategies** — a named bundle of flow + role/context/budget overlays on
  top of `repo.yml` (see [Strategies](#strategies)), picked by a small
  rule-based selector at `define` and `approve`, or pinned with
  `--strategy <name>`. Every run records which strategy was used and a
  content hash of it. Single-repo only for now — `WorkspaceEngine` doesn't
  select a strategy yet, and reselecting after a `reopen` is deferred (no
  `approve` step to hook into).
- **Structured role output** — the planner's `BRANCH:` line was replaced
  outright by a fenced YAML block (`branch`/`phases`/`risk_level`, see
  [Structured role output](#structured-role-output)); the reviewer keeps its
  `REVIEW:` gate unchanged but can add an optional YAML block
  (`confidence`/`blocking_issues`/`non_blocking_issues`/
  `suggested_next_role`, informational only); and a new **`verifier`** role
  runs after the reviewer with its own `VERIFY: passed|failed` gate — a
  `failed` verdict loops back to the implementer, sharing the same
  `max_fix_iterations` budget as the review gate rather than a separate
  counter. Verifier is single-repo only for now.
- **Phased execution** — a flow can declare a `phase_flow` (see [Phased
  execution](#phased-execution)) walked once per planner phase; `simple-change`
  is unaffected (opt-in only), `phased-documentation` uses it. All phases'
  fix loops share one budget; the verifier still runs once at the end, and
  redoes just the last phase (not the whole plan) if it fails.

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
