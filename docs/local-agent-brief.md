# Local Agent Brief — workitem-conductor

This document summarizes the current thinking behind `workitem-conductor` so a local coding agent can continue the implementation from a clear starting point.

## Project name

Repository: `workitem-conductor`

CLI command: `conductor`

Short tagline:

```text
Define the goal. Let agents do the loop. Review the result.
```

## Core idea

`workitem-conductor` is a local conductor for AI-assisted development workflows.

It is **not** intended to be another coding agent.

It is intended to coordinate existing coding agents and model providers, such as Codex CLI, Claude CLI, Ollama, or API-backed models.

The user should define the goal and validate the final result. The conductor should replace the human in the operational loop: copying prompts, passing handoffs, reading outputs, deciding the next role, and repeating implement/review/fix cycles.

## Product statement

The user interacts with the conductor until the goal is clear. After that, the user says something equivalent to "go" or "execute". The conductor then runs the workflow by itself until one of two things happens:

1. the workitem reaches a valid final state and is ready for human review;
2. the conductor hits a stop condition and asks for human input.

The desired user experience is:

```text
I define the goal.
The conductor runs the agent loop.
I drink coffee or work on something else.
The conductor calls me only when blocked or done.
```

## Why this exists

The current manual workflow is roughly:

1. open a coding agent CLI, usually Codex;
2. discuss a problem conversationally until the goal is understood;
3. ask the agent to create a workitem and artifacts;
4. ask the main agent to prepare handoffs;
5. copy handoffs into other agents, such as implementer or reviewer;
6. read the result;
7. return to the main agent and ask it to validate and create the next handoff;
8. repeat for backend, frontend, integration, review, fixes and possible smoke tests.

The project exists to remove the human from this repetitive copy/paste and routing role.

## Important distinction

This tool should be a conductor, not a model.

```text
Role       = responsibility in the workflow, such as implementer or reviewer.
Provider   = execution backend, such as Codex CLI, Claude CLI, Ollama or an API model.
Flow       = ordered process, such as plan -> implement -> review -> fix -> test.
Workitem   = unit of intent, evidence, state and final validation.
Session    = sandbox/runtime environment for the workitem.
```

A role must not be hardcoded to a provider. For example, `reviewer` can run on Claude today and on another model tomorrow.

## Expected high-level flow

The intended high-level flow is:

```text
goal definition
-> goal contract
-> workitem creation
-> optional sandbox/session creation
-> planner
-> implementer
-> reviewer
-> fix loop when needed
-> integration review when needed
-> local tests/checks
-> sandbox smoke test when configured
-> final report
-> human validation
```

The first implementation does not need to support all of this. It should be designed so this is the direction.

## Goal definition mode

Before autonomous execution, the conductor should help shape the goal.

The user can interact freely until the work is clear. The conductor can inspect the repository and ask questions when necessary.

The output of this phase should be a compact goal contract.

Possible fields:

```yaml
goal: "Short statement of the intended change"
scope:
  include: []
  exclude: []
acceptance_criteria: []
constraints: []
stop_conditions: []
```

No autonomous execution should start before the goal is sufficiently clear.

## Execution mode

After goal definition, the user should be able to tell the conductor to execute.

The conductor should then:

1. select a flow;
2. select the next role;
3. prepare context for that role;
4. call the configured provider;
5. capture the output;
6. update workitem state;
7. decide the next step;
8. repeat until done or blocked.

The important behavior is not simply generating prompts. The conductor must own the loop.

## Stop conditions

The conductor should stop and ask for human input when:

- the same blocker repeats too many times;
- the workflow exceeds the configured max iterations;
- tests keep failing after repeated fixes;
- the implementation requires changing the approved goal or scope;
- a provider fails repeatedly;
- the next action is ambiguous;
- reviewers and implementers disagree repeatedly;
- secrets, credentials or production access are required;
- the change appears risky enough to require explicit human approval.

The default behavior should be safe: stop rather than improvise beyond the approved goal.

## Repository usage model

The tool should be installed globally or run from source, but used from inside a target repository or workspace.

Example:

```bash
cd ~/projects/my-service
conductor init
conductor define "fix the policy discovery bug"
conductor execute
```

The tool should be repository-agnostic. It should work for different repositories and languages, such as:

- Python backend;
- TypeScript frontend;
- C++ project;
- documentation-only repository;
- multi-repository workspace.

Repository-specific behavior should live in local `.ai/` configuration.

## Files created in target repositories

The conductor should create project metadata under `.ai/` in the target repository or workspace.

Suggested split:

```text
.ai/
  repo.yml                 # versionable repo configuration
  instructions.md          # versionable repo-specific guidance
  flows/                   # versionable flow definitions
  roles/                   # versionable role prompts/instructions
  hooks/                   # versionable deterministic checks

  workitems/               # runtime/history, ignored by default
  sessions/                # sandbox/session data, ignored by default
  runs/                    # provider transcripts/logs, ignored by default
  cache/                   # temporary context/cache, ignored by default
```

Default `.gitignore` recommendation:

```gitignore
.ai/workitems/
.ai/sessions/
.ai/runs/
.ai/cache/
```

Versionable configuration should be separated from runtime artifacts.

## Multi-repository workspaces

The long-term design should support both single-repo and multi-repo work.

Single repo:

```text
my-repo/
  .ai/
    repo.yml
    workitems/
    sessions/
```

Multi-repo workspace:

```text
workspace-root/
  .ai/
    workspace.yml
    workitems/
    sessions/

  backend-repo/
    .ai/repo.yml

  frontend-repo/
    .ai/repo.yml
```

The conductor may be called from inside one repo but should be able to find the workspace configuration if present.

## Sandbox/session idea

A session is the execution environment for a workitem.

Inspired by context-first workflows, a future session may include:

- git worktrees;
- one or more repositories;
- generated Docker Compose files;
- dynamic ports;
- isolated databases/caches;
- smoke test metadata;
- local runtime context.

Important conceptual split:

```text
workitem = memory, state, evidence and governance
session  = execution sandbox and runtime isolation
```

The first milestone does not need full sandbox support, but the architecture should not block it.

## Provider model

The conductor should support different provider types.

### CLI provider with external authentication

The conductor should be able to call an already configured CLI, such as Codex CLI or Claude CLI, without managing authentication itself.

The CLI account/session is configured outside the conductor.

The conductor only needs to:

- check if the command exists;
- call the command;
- pass prompt/context;
- capture output;
- handle errors;
- stop if the CLI reports it is not authenticated.

It should not read or manage provider tokens.

### API provider

For API-backed models, the conductor can read API keys from environment variables.

Example concept:

```yaml
providers:
  qwen_api:
    type: api
    base_url: "https://example.com/v1"
    model: "qwen-coder"
    api_key_env: "QWEN_API_KEY"
```

### Local provider

For local models, the conductor may call Ollama or another local runtime.

Example concept:

```yaml
providers:
  local_qwen:
    type: ollama
    model: qwen2.5-coder
```

## Provider invocation preferences

Order of preference for automation:

1. direct API when appropriate;
2. CLI headless/non-interactive mode;
3. CLI interactive mode through a pseudo-terminal;
4. UI automation only as a last resort.

The goal is not literal clipboard automation if a cleaner invocation mode exists. The goal is to remove the human from the copy/paste loop.

## Context and token strategy

The conductor may reduce token usage if it does context packing well.

Potential strategies:

- send only relevant files/context for each role;
- pass compact summaries between roles;
- extract blockers from reviews into structured state;
- avoid sending full transcripts repeatedly;
- use local or cheaper models for summarization and log parsing;
- keep a compact `state.yml` or `state.json` instead of relying only on markdown transcripts.

Having a CLI alone does not reduce tokens. The saving comes from selective context and structured state.

## Suggested internal architecture

Initial package direction:

```text
src/conductor/
  cli.py
  core/
    engine.py
    flow.py
    state.py
    stop_conditions.py
  workitems/
    manager.py
    models.py
  providers/
    base.py
    cli_runner.py
    api_runner.py
    ollama_runner.py
  config/
    loader.py
    models.py
  sessions/
    manager.py
  templates/
```

Do not overbuild this immediately. Start with a small working conductor loop.

## Suggested first milestone

The first useful milestone should be:

```text
Given a goal in a repository, create a workitem and run a simple local flow skeleton.
```

Minimum commands:

```bash
conductor init
conductor define "some goal"
conductor status
```

Next commands:

```bash
conductor execute
conductor doctor
```

The first `execute` implementation may be semi-automatic or stubbed, but it should establish the workitem state model and artifact layout.

## Suggested first implementation steps

1. Clean the current initial structure if needed.
2. Implement `conductor init` so it creates `.ai/` in the current repo with:
   - `.ai/repo.yml`
   - `.ai/instructions.md`
   - `.ai/flows/simple-change.yml`
   - `.ai/roles/planner.md`
   - `.ai/roles/implementer.md`
   - `.ai/roles/reviewer.md`
3. Implement a workitem id generator.
4. Implement `conductor define <goal>` to create:
   - `.ai/workitems/<id>/goal.yml`
   - `.ai/workitems/<id>/status.yml`
5. Implement `conductor status` to show the active/latest workitem.
6. Implement a basic provider abstraction, initially with a dry-run provider that writes the prompt to a file.
7. Implement `conductor execute --dry-run` to generate the next handoff instead of calling a real model.
8. Add real provider adapters only after the flow/state model is stable.

## Recommended near-term behavior

Do not try to make the first version fully autonomous.

First prove:

```text
state model + artifact layout + flow selection + next handoff generation
```

Then add:

```text
provider calls + output ingestion + review/fix loop
```

Then add:

```text
sandbox/session/worktree/docker compose
```

## Important design constraint

The project should remain independent of any specific repository or company.

It should not be hardcoded for Habit, TPA Claims, backend, frontend or any one stack.

It should be a generic conductor whose behavior is taught by local `.ai/` configuration.

## Current repository state note

The current repository has an initial skeleton, but it may not be the desired final structure. It is acceptable to reorganize it.

Prefer making the structure simple and coherent over preserving the first draft.

## Final guiding sentence

The user should not be the clipboard between agents.

`workitem-conductor` exists so the user can define a goal, let the conductor run the operational loop, and return only for decisions or final validation.
