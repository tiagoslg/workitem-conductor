"""Default ``.ai/`` content written by ``conductor init``.

The split is deliberate: everything written here is *versionable* configuration
that teaches the conductor how to work in this repo (config, flow, role prompts).
Runtime artifacts (workitems/, sessions/, runs/, cache/) are created lazily and
git-ignored via the ``.ai/.gitignore`` written below.

``scaffold_ai`` is idempotent: it never overwrites a file the user may have
edited; it only creates what is missing and reports created vs skipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

REPO_YML = """\
# workitem-conductor repo configuration (versionable)
name: TODO
default_flow: simple-change

# Map each role to a provider. Providers are not implemented yet (MVP 2),
# but roles are referenced by name so they can be re-pointed without changing
# the flow. Example:
#
# providers:
#   codex_cli:
#     type: cli_one_shot
#     command: codex
#   claude_cli:
#     type: cli_one_shot
#     command: claude
#   qwen_cli:
#     type: cli_one_shot              # Qwen Code CLI (headless one-shot)
#     command: qwen
#     args: ["--approval-mode", "yolo"]   # omit/use "plan" for read-only roles
#     prompt_via: arg
#   qwen_api:
#     type: api                      # OpenAI-compatible HTTP endpoint
#     base_url: https://api.example.com/v1
#     model: qwen2.5-coder
#     api_key_env: QWEN_API_KEY      # key is read from this env var, never stored
#   local_qwen:
#     type: ollama                   # native local Ollama (no key)
#     model: qwen2.5-coder           # base_url defaults to http://localhost:11434
#
# roles:
#   refiner:     { provider: codex_cli }   # used by `conductor refine`
#   planner:     { provider: codex_cli }
#   implementer: { provider: codex_cli }
#   reviewer:    { provider: claude_cli }
#
# refine:
#   max_question_rounds: 5   # cap on clarifying-question rounds in `refine`
providers: {}
roles: {}
"""

INSTRUCTIONS_MD = """\
# Repository instructions

Repository-specific guidance for the conductor and the agents it drives.
Keep this short and concrete. Examples of what belongs here:

- how to run the test suite and linters;
- coding conventions the implementer must follow;
- areas that require extra care or explicit human approval;
- commands the reviewer should use to validate changes.
"""

SIMPLE_CHANGE_FLOW = """\
# Flow: simple-change
# The smallest loop that preserves safety. Steps reference roles by name only.
name: simple-change
description: Plan, implement, review (with a fix loop), validate, report.
steps:
  - role: planner
    stage: planning
  - role: implementer
    stage: implementing
  - role: reviewer
    stage: reviewing
    # On a `changes_requested` verdict the conductor loops back to the
    # implementer (stage `fixing`) and re-reviews, up to max_fix_iterations.
    gate: review
    on_changes: implementer
  - role: validator
    stage: validating
max_fix_iterations: 3
"""

PLANNER_MD = """\
# Role: planner

You turn an approved goal contract into a concrete, low-ambiguity plan that an
implementer can execute with minimal independent decisions.

## Inputs
- the goal contract (goal, scope, acceptance criteria, constraints, stop conditions);
- the repository and its instructions.

## Output
The **first line** of your response must be:

    BRANCH: feat/<kebab-slug>

where `<kebab-slug>` is a short, lowercase, hyphen-separated name derived from
the goal (e.g. `feat/add-human-readable-size`). The conductor reads it to
suggest a working branch before implementation starts.

Then write the implementation plan. Assume the implementer **cannot read the
repository** — it will only see this plan, the goal contract, and nothing else.
Every decision the implementer needs to make must be answered here.

The plan must cover at minimum:
- objective and current state (what exists, what is broken or missing);
- exact files to create or modify, with their relative paths;
- for each file: the specific changes to make (functions to add/change/delete,
  interfaces to define, imports to add);
- ordered implementation tasks — concrete enough to follow without judgement;
- tests to add or update, with the expected behaviour to verify;
- risks and acceptance criteria cross-check;
- any ambiguity that requires stopping to ask the human.

## Rules
- always emit `BRANCH: feat/<kebab-slug>` as the very first line — no preamble;
- do not write production code — write instructions precise enough that someone
  else can write it correctly the first time;
- do not expand the approved scope — surface scope changes as a stop condition;
- prefer the smallest plan that satisfies the acceptance criteria;
- name every file path explicitly; do not say "update the relevant files".
"""

IMPLEMENTER_MD = """\
# Role: implementer

You implement the approved plan within the approved scope.

## Inputs
- the goal contract and the plan;
- review feedback, when looping back for fixes.

## Output
- the code change;
- implementation notes: what changed, why, and how it was verified.

## Rules
- stay within scope; if the change requires altering approved scope, stop and
  flag it rather than improvising;
- follow the repository instructions and conventions;
- keep changes focused and reviewable;
- run available tests/checks and report the result honestly.
"""

REVIEWER_MD = """\
# Role: reviewer

You review the implementation against the goal contract and the plan.

## Output
A review that ends with exactly one verdict line, on its own line:

    REVIEW: approved
    REVIEW: changes_requested

- `approved` — acceptance criteria met, no blocking issues;
- `changes_requested` — precede the line with a short, explicit list of
  blocking issues to fix. The conductor loops back to the implementer with
  your feedback, up to the flow's max fix iterations.

## Rules
- be specific and actionable; each blocker should be independently fixable;
- check correctness against acceptance criteria first, then quality;
- do not request changes outside the approved scope;
- always emit the verdict line — it drives the fix loop;
- if you and the implementer disagree irreconcilably, flag it for the human.
"""

REFINER_MD = """\
# Role: refiner

You help turn a rough goal into a precise, low-ambiguity **goal contract** before
any implementation starts. You read the goal and explore the repository to ground
your understanding, then either ask focused questions or write the contract.

## How you are driven

The conductor calls you in rounds and reads your output for exactly one marker
line. Emit one — and only one — per response, on its own line:

- When you still need information that would materially change the contract:

      QUESTIONS:
      1. <question>
      2. <question>

  Ask only what matters. Prefer zero questions when the repo and goal already
  make the contract clear. The conductor collects the answers and calls you again
  with them appended.

- When you can write the contract:

      CONTRACT:
      ```yaml
      scope:
        include: []
        exclude: []
      acceptance_criteria: []
      constraints: []
      validation: []        # how the result will be checked
      stop_conditions: []   # when to stop and ask the human
      ```

## Rules

- ground the contract in what the repository actually contains;
- keep scope to the smallest that satisfies the goal — never invent work;
- make acceptance criteria concrete and checkable;
- do not restate or change the `goal` text and do not approve anything;
- **start your reply with the marker line** (`QUESTIONS:` or `CONTRACT:`) — no
  preamble or commentary before it; it drives the loop;
- ask at most one round or two of questions; once you have enough to be useful,
  **write the CONTRACT** rather than asking for more — a good-enough contract the
  human can edit beats an endless interview;
- YAML string values must not contain TypeScript syntax or flow indicators
  (`{`, `}`, `|`, `?` used as type operators) — describe types in plain English
  (e.g. "string or number", not "string | number");
- any YAML value that contains a colon followed by a space (`: `) must be
  enclosed in single or double quotes.
"""

WORKSPACE_CONFIG_YML = """\
# workitem-conductor workspace configuration
# Providers and roles used for cross-project workitems in this workspace.
# Same format as .ai/repo.yml in individual repos.
#
# providers:
#   claude_cli:
#     type: cli_one_shot
#     command: claude
#     args: ["-p"]
#     prompt_via: arg
#
# roles:
#   refiner: { provider: claude_cli }
providers: {}
roles: {}
"""

WORKSPACE_INSTRUCTIONS_MD = """\
# Workspace instructions: {name}

Cross-project context for the refiner and planner.
Describe:
- how the projects in this workspace relate to each other
  (e.g. the FE calls the BE REST API at /api/v1/...);
- shared conventions that apply across all repos;
- any context a human analyst would need to reason about cross-project bugs.

Keep this short and concrete.
"""

AI_GITIGNORE = """\
# Runtime artifacts — not versioned by default.
workitems/
sessions/
runs/
cache/
active_workitem.txt
"""


@dataclass
class ScaffoldResult:
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


# (relative path within .ai/, content)
_FILES: tuple[tuple[str, str], ...] = (
    ("repo.yml", REPO_YML),
    ("instructions.md", INSTRUCTIONS_MD),
    ("flows/simple-change.yml", SIMPLE_CHANGE_FLOW),
    ("roles/planner.md", PLANNER_MD),
    ("roles/implementer.md", IMPLEMENTER_MD),
    ("roles/reviewer.md", REVIEWER_MD),
    ("roles/refiner.md", REFINER_MD),
    (".gitignore", AI_GITIGNORE),
)


def scaffold_workspace(root: Path, name: str) -> ScaffoldResult:
    """Create workspace config skeleton under ``root`` idempotently.

    ``root`` is the workspace directory itself
    (``~/.config/conductor/workspaces/<name>/``). Existing files are kept.
    """
    result = ScaffoldResult()
    root.mkdir(parents=True, exist_ok=True)
    files = [
        ("config.yml", WORKSPACE_CONFIG_YML),
        ("instructions.md", WORKSPACE_INSTRUCTIONS_MD.format(name=name)),
    ]
    for rel, content in files:
        target = root / rel
        if target.exists():
            result.skipped.append(rel)
        else:
            target.write_text(content, encoding="utf-8")
            result.created.append(rel)
    return result


def scaffold_ai(root: Path) -> ScaffoldResult:
    """Create the versionable ``.ai/`` skeleton under ``root`` idempotently.

    ``root`` is the ``.ai/`` directory itself. Existing files are left untouched.
    """
    result = ScaffoldResult()
    root.mkdir(parents=True, exist_ok=True)
    project_name = root.parent.name
    for rel, content in _FILES:
        if rel == "repo.yml":
            content = content.replace("name: TODO", f"name: {project_name}", 1)
        target = root / rel
        if target.exists():
            result.skipped.append(rel)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        result.created.append(rel)
    return result
