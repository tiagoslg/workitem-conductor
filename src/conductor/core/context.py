"""Assemble the prompt/context handed to a role for one step.

Context is built from curated sources by default: role instructions, goal
contract, and — the antidote to unbounded growth across reopens — a memory
section written by the ``summarizer`` role rather than raw prior outputs.
Raw prior-step outputs (every role's most recent output, capped per-output)
are available as an explicit opt-in (``ContextConfig.include_raw_outputs``)
for repos that don't yet trust their summarizer. See ``config.models.ContextConfig``
for the full set of toggles and the overall prompt character budget.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..config.models import ContextConfig
from ..paths import AiPaths
from ..workitems.manager import Workitem, load_memory
from .runs import StepRecord
from .worktree import working_tree_diff

if TYPE_CHECKING:
    from ..paths import WorkspacePaths

_FALLBACK_ROLE_PROMPT = (
    "# Role: {role}\n\n"
    "You are the **{role}** for this workitem. Act within the approved goal and "
    "scope, produce a clear work product, and flag anything that should stop and "
    "ask the human.\n"
)

_MAX_OUTPUT_CHARS = 64_000  # ~16k tokens; large enough for detailed plans and reviews


def load_role_prompt(paths: AiPaths | WorkspacePaths, role: str) -> str:
    """Return the role's instructions from ``roles/<role>.md``.

    Accepts both ``AiPaths`` (.ai/roles/) and ``WorkspacePaths`` (workspace/roles/).
    Falls back to a generic instruction when no file exists.
    """
    role_file = paths.roles_dir / f"{role}.md"
    if role_file.is_file():
        return role_file.read_text(encoding="utf-8")
    return _FALLBACK_ROLE_PROMPT.format(role=role)


def build_cross_project_section(ws_paths: WorkspacePaths) -> str:
    """Build the cross-project context block for workspace refine/plan prompts.

    Includes the workspace instructions (if any) and each project's .ai/instructions.md
    (labeled by project name and path), so the refiner/planner can reason across repos.
    """
    from pathlib import Path as _Path
    from ..paths import AI_DIRNAME

    parts: list[str] = []

    if ws_paths.instructions.is_file():
        parts.append("## Workspace instructions\n")
        parts.append(ws_paths.instructions.read_text(encoding="utf-8").rstrip())

    parts.append("\n## Projects in this workspace\n")
    for repo_path in ws_paths.project_roots:
        label = repo_path.name
        parts.append(f"### {label}\n- path: `{repo_path}`")
        instructions = repo_path / AI_DIRNAME / "instructions.md"
        if instructions.is_file():
            parts.append(
                "- instructions:\n\n"
                + instructions.read_text(encoding="utf-8").rstrip()
            )
        else:
            parts.append("- instructions: _(none — run `conductor init` in this repo)_")

    return "\n".join(parts)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return text[:limit] + f"\n\n[... truncated — {omitted} chars omitted ...]\n"


def _latest_outputs_by_role(outputs_dir: Path) -> tuple[dict[str, tuple[int, Path]], list[str]]:
    """Scan ``outputs/*.output.md``, keeping the highest-seq file per role.

    Shared by ``_prior_outputs`` (all roles) and ``_latest_output_for_role``
    (one role) so there is exactly one place that parses the ``NN-role``
    filename convention.
    """
    by_role: dict[str, tuple[int, Path]] = {}
    order: list[str] = []  # first-appearance order
    if not outputs_dir.is_dir():
        return by_role, order

    for path in sorted(outputs_dir.glob("*.output.md")):
        stem = path.name  # e.g. "03-implementer.output.md"
        try:
            dash = stem.index("-")
            seq = int(stem[:dash])
        except ValueError:
            continue
        role = stem[dash + 1:].removesuffix(".output.md")
        if role not in by_role:
            order.append(role)
        by_role[role] = (seq, path)
    return by_role, order


def _prior_outputs(workitem: Workitem) -> list[tuple[str, str]]:
    """Most recent output per role, in first-appearance order, size-capped.

    When a role appears multiple times (fix loop), only the highest-NN file
    is kept — earlier rounds are noise because the agent works from the
    actual repo, not from its prior notes.
    """
    by_role, order = _latest_outputs_by_role(workitem.directory / "outputs")
    results = []
    for role in order:
        _, path = by_role[role]
        text = path.read_text(encoding="utf-8")
        results.append((path.name, _truncate(text, _MAX_OUTPUT_CHARS)))
    return results


def _latest_output_for_role(workitem: Workitem, role: str) -> str | None:
    """The single most recent output for ``role``, or ``None`` if it hasn't run yet."""
    by_role, _ = _latest_outputs_by_role(workitem.directory / "outputs")
    entry = by_role.get(role)
    if entry is None:
        return None
    _, path = entry
    return _truncate(path.read_text(encoding="utf-8"), _MAX_OUTPUT_CHARS)


def _memory_section(paths: AiPaths, workitem: Workitem) -> str | None:
    """A curated ``## Memory`` section from ``memory.yml``, or ``None`` if empty."""
    memory = load_memory(paths, workitem.workitem_id)
    if not memory.current_summary and not memory.open_issues and not memory.decisions:
        return None
    parts = ["\n## Memory\n"]
    if memory.current_summary:
        parts.append(memory.current_summary.strip())
    if memory.open_issues:
        parts.append("\n**Open issues:**")
        parts.extend(f"- {i}" for i in memory.open_issues)
    if memory.decisions:
        parts.append("\n**Recent decisions:**")
        parts.extend(f"- {d.decision}" for d in memory.decisions[-3:])
    return "\n".join(parts)


def _diff_section(execution_cwd: Path | None) -> str | None:
    """A ``git diff --stat`` of ``execution_cwd`` so far, or ``None`` if unavailable."""
    if execution_cwd is None:
        return None
    diff_text = working_tree_diff(execution_cwd)
    if not diff_text or not diff_text.strip():
        return None
    return "\n## Working tree diff so far\n\n```\n" + diff_text.strip() + "\n```"


def _last_review_section(workitem: Workitem) -> str | None:
    """Just the reviewer's latest output, not every role's — for a tight fix loop."""
    text = _latest_output_for_role(workitem, "reviewer")
    if not text:
        return None
    return "\n## Latest reviewer output\n\n" + text.rstrip() + "\n"


def build_workspace_planner_context(ws_paths: "WorkspacePaths", workitem: Workitem) -> str:
    """Compose the planner prompt for a workspace workitem (cross-project).

    The planner receives the full cross-project context so it can produce a
    per-project plan. Its output is later injected into each project's
    implementer/reviewer context.
    """
    parts: list[str] = [load_role_prompt(ws_paths, "planner").rstrip()]
    parts.append("\n---\n")
    parts.append(build_cross_project_section(ws_paths))

    parts.append("\n---\n## Workitem\n")
    parts.append(f"- id: {workitem.workitem_id}")
    parts.append(f"- title: {workitem.state.title}")

    parts.append("\n## Goal contract\n")
    parts.append("```yaml\n" + workitem.goal.to_yaml().rstrip() + "\n```")

    reopen_file = workitem.directory / "reopen.md"
    if reopen_file.is_file():
        parts.append("\n## Reopen reason\n")
        parts.append(reopen_file.read_text(encoding="utf-8").strip())

    parts.append(
        "\n## Your task\n"
        "Act as the **planner** and produce a per-project implementation plan. "
        "Write one `## Project: <name>` section per entry in `target_projects`. "
        "Each section must be self-contained — the implementer for that project "
        "only sees its own section."
    )
    return "\n".join(parts) + "\n"


def build_workspace_project_context(
    project_paths: AiPaths,
    workitem: Workitem,
    role: str,
    *,
    workspace_plan: str | None = None,
    prior_implementer_output: str | None = None,
    fix_iteration: int = 0,
) -> str:
    """Compose the implementer or reviewer prompt for one project in a workspace run.

    ``workspace_plan`` is the workspace planner's full output (truncated to
    _MAX_OUTPUT_CHARS). ``prior_implementer_output`` carries the most recent
    implementer output for the reviewer or for a fix-loop round.
    """
    parts: list[str] = [load_role_prompt(project_paths, role).rstrip()]

    parts.append("\n---\n## Workitem\n")
    parts.append(f"- id: {workitem.workitem_id}")
    parts.append(f"- title: {workitem.state.title}")

    parts.append("\n## Goal contract\n")
    parts.append("```yaml\n" + workitem.goal.to_yaml().rstrip() + "\n```")

    if workspace_plan:
        parts.append("\n## Cross-project implementation plan\n")
        parts.append(_truncate(workspace_plan, _MAX_OUTPUT_CHARS).rstrip())

    if prior_implementer_output:
        header = (
            f"\n## Implementer output (fix round {fix_iteration})\n"
            if fix_iteration > 0
            else "\n## Implementer output\n"
        )
        parts.append(header)
        parts.append(_truncate(prior_implementer_output, _MAX_OUTPUT_CHARS).rstrip())

    parts.append(
        "\n## Your task\n"
        f"Act as the **{role}** and produce your work product now."
    )
    return "\n".join(parts) + "\n"


def build_context(
    paths: AiPaths,
    workitem: Workitem,
    role: str,
    *,
    context_config: ContextConfig | None = None,
    execution_cwd: Path | None = None,
) -> str:
    """Compose the full prompt text for ``role`` on ``workitem``.

    ``context_config`` controls which optional sections are included and the
    overall prompt character budget (see ``ContextConfig``); defaults to the
    same values as an unset ``context:`` block in ``repo.yml``. Optional
    sections (memory, diff, last review, raw outputs) are budget-trimmed from
    the tail if needed — the fixed prefix (role/goal/reopen reason) and the
    trailing task instruction are never truncated.
    """
    cfg = context_config or ContextConfig()

    prefix_parts: list[str] = [load_role_prompt(paths, role).rstrip()]
    prefix_parts.append("\n---\n## Workitem\n")
    prefix_parts.append(f"- id: {workitem.workitem_id}")
    prefix_parts.append(f"- title: {workitem.state.title}")

    prefix_parts.append("\n## Goal contract\n")
    prefix_parts.append("```yaml\n" + workitem.goal.to_yaml().rstrip() + "\n```")

    reopen_file = workitem.directory / "reopen.md"
    if reopen_file.is_file():
        prefix_parts.append("\n## Reopen reason\n")
        prefix_parts.append(reopen_file.read_text(encoding="utf-8").strip())

    # Ordered most to least essential — the tail is what gets trimmed under budget.
    optional_parts: list[str] = []
    if cfg.include_memory:
        section = _memory_section(paths, workitem)
        if section:
            optional_parts.append(section)
    if cfg.include_last_diff:
        section = _diff_section(execution_cwd)
        if section:
            optional_parts.append(section)
    if cfg.include_last_review:
        section = _last_review_section(workitem)
        if section:
            optional_parts.append(section)
    if cfg.include_raw_outputs:
        prior = _prior_outputs(workitem)
        if prior:
            block = ["\n## Prior step outputs\n"]
            if workitem.state.fix_iterations > 0:
                block.append(
                    f"> Fix iteration {workitem.state.fix_iterations}. "
                    "Showing most recent output per role only "
                    "(earlier rounds omitted).\n"
                )
            for name, text in prior:
                block.append(f"### {name}\n\n{text.rstrip()}\n")
            optional_parts.append("\n".join(block))

    task_suffix = (
        "\n## Your task\n"
        f"Act as the **{role}** and produce your work product now.\n"
    )

    prefix = "\n".join(prefix_parts) + "\n"
    optional_text = "\n".join(optional_parts)

    budget_for_optional = cfg.max_prompt_chars - len(prefix) - len(task_suffix)
    if optional_text and budget_for_optional <= 0:
        # No room for any of it — still leave a marker so a human reading the
        # prompt knows something was cut, rather than silently vanishing.
        optional_text = f"\n\n[... budget truncated — {len(optional_text)} chars omitted ...]\n"
    elif optional_text and len(optional_text) > budget_for_optional:
        omitted = len(optional_text) - budget_for_optional
        marker = f"\n\n[... budget truncated — {omitted} chars omitted ...]\n"
        keep = max(budget_for_optional - len(marker), 0)
        optional_text = optional_text[:keep] + marker

    return prefix + optional_text + task_suffix


def build_summarizer_context(
    paths: AiPaths,
    workitem: Workitem,
    trigger: str,
    run_steps: list[StepRecord],
    execution_cwd: Path | None,
) -> str:
    """Compose the prompt for the ``summarizer`` role.

    ``trigger`` (``"finish"`` | ``"stop"`` | ``"loop_back"`` | ``"reopen"``)
    tells the summarizer why it's being asked to update memory right now.
    """
    parts: list[str] = [load_role_prompt(paths, "summarizer").rstrip()]

    parts.append("\n---\n## Workitem\n")
    parts.append(f"- id: {workitem.workitem_id}")
    parts.append(f"- title: {workitem.state.title}")
    parts.append(f"- trigger: {trigger}")
    parts.append(f"- stage: {workitem.state.stage}")
    parts.append(f"- status: {workitem.state.status}")

    parts.append("\n## Goal contract\n")
    parts.append("```yaml\n" + workitem.goal.to_yaml().rstrip() + "\n```")

    reopen_file = workitem.directory / "reopen.md"
    if reopen_file.is_file():
        parts.append("\n## Reopen reason\n")
        parts.append(reopen_file.read_text(encoding="utf-8").strip())

    existing_memory = load_memory(paths, workitem.workitem_id)
    if existing_memory.current_summary or existing_memory.open_issues or existing_memory.decisions:
        parts.append("\n## Existing memory\n")
        parts.append("```yaml\n" + existing_memory.to_yaml().rstrip() + "\n```")

    if run_steps:
        parts.append("\n## Steps in this run so far\n")
        for step in run_steps:
            verdict = f" → {step.verdict}" if step.verdict else ""
            mark = "ok" if step.ok else "FAILED"
            parts.append(f"- {step.role} via {step.provider}: {mark}{verdict} (`{step.output_path}`)")

    diff_section = _diff_section(execution_cwd)
    if diff_section:
        parts.append(diff_section)

    parts.append(
        "\n## Your task\n"
        "Act as the **summarizer**. Read the above and emit exactly one "
        "`SUMMARY:` block updating the workitem's memory."
    )
    return "\n".join(parts) + "\n"
