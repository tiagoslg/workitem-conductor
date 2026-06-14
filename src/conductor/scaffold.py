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
#   qwen_api:
#     type: api                      # OpenAI-compatible HTTP endpoint
#     base_url: https://api.example.com/v1
#     model: qwen2.5-coder
#     api_key_env: QWEN_API_KEY      # key is read from this env var, never stored
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
A plan covering, at minimum:
- objective and current state;
- in-scope and out-of-scope work;
- impacted files/modules;
- ordered implementation tasks;
- tests to add or update;
- risks and acceptance criteria;
- any ambiguity that should stop and ask the human.

## Rules
- do not write production code;
- do not expand the approved scope — surface scope changes as a stop condition;
- prefer the smallest plan that satisfies the acceptance criteria.
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
- always emit a marker line — it drives the loop.
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


def scaffold_ai(root: Path) -> ScaffoldResult:
    """Create the versionable ``.ai/`` skeleton under ``root`` idempotently.

    ``root`` is the ``.ai/`` directory itself. Existing files are left untouched.
    """
    result = ScaffoldResult()
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in _FILES:
        target = root / rel
        if target.exists():
            result.skipped.append(rel)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        result.created.append(rel)
    return result
