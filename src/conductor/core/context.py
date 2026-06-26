"""Assemble the prompt/context handed to a role for one step.

Context is built selectively: role instructions, goal contract, and the most
recent output per role from prior steps. Older iterations of the same role are
dropped (the agent reads the actual repo files, not its own prior notes).
Each prior output is capped at _MAX_OUTPUT_CHARS to keep prompts bounded.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..paths import AiPaths
from ..workitems.manager import Workitem

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


def _prior_outputs(workitem: Workitem) -> list[tuple[str, str]]:
    """Most recent output per role, in first-appearance order, size-capped.

    Filenames follow ``NN-<role>.output.md``. When a role appears multiple times
    (fix loop), only the highest-NN file is kept — earlier rounds are noise
    because the agent works from the actual repo, not from its prior notes.
    Each output is truncated to ``_MAX_OUTPUT_CHARS`` with a marker.
    """
    outputs_dir = workitem.directory / "outputs"
    if not outputs_dir.is_dir():
        return []

    by_role: dict[str, tuple[int, Path]] = {}  # role → (seq, path)
    order: list[str] = []  # first-appearance order

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

    results = []
    for role in order:
        _, path = by_role[role]
        text = path.read_text(encoding="utf-8")
        if len(text) > _MAX_OUTPUT_CHARS:
            omitted = len(text) - _MAX_OUTPUT_CHARS
            text = (
                text[:_MAX_OUTPUT_CHARS]
                + f"\n\n[... truncated — {omitted} chars omitted ...]\n"
            )
        results.append((path.name, text))
    return results


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
        plan_text = workspace_plan
        if len(plan_text) > _MAX_OUTPUT_CHARS:
            omitted = len(plan_text) - _MAX_OUTPUT_CHARS
            plan_text = (
                plan_text[:_MAX_OUTPUT_CHARS]
                + f"\n\n[... truncated — {omitted} chars omitted ...]\n"
            )
        parts.append("\n## Cross-project implementation plan\n")
        parts.append(plan_text.rstrip())

    if prior_implementer_output:
        header = (
            f"\n## Implementer output (fix round {fix_iteration})\n"
            if fix_iteration > 0
            else "\n## Implementer output\n"
        )
        impl_text = prior_implementer_output
        if len(impl_text) > _MAX_OUTPUT_CHARS:
            omitted = len(impl_text) - _MAX_OUTPUT_CHARS
            impl_text = (
                impl_text[:_MAX_OUTPUT_CHARS]
                + f"\n\n[... truncated — {omitted} chars omitted ...]\n"
            )
        parts.append(header)
        parts.append(impl_text.rstrip())

    parts.append(
        "\n## Your task\n"
        f"Act as the **{role}** and produce your work product now."
    )
    return "\n".join(parts) + "\n"


def build_context(paths: AiPaths, workitem: Workitem, role: str) -> str:
    """Compose the full prompt text for ``role`` on ``workitem``."""
    parts: list[str] = []
    parts.append(load_role_prompt(paths, role).rstrip())

    parts.append("\n---\n## Workitem\n")
    parts.append(f"- id: {workitem.workitem_id}")
    parts.append(f"- title: {workitem.state.title}")

    parts.append("\n## Goal contract\n")
    parts.append("```yaml\n" + workitem.goal.to_yaml().rstrip() + "\n```")

    reopen_file = workitem.directory / "reopen.md"
    if reopen_file.is_file():
        parts.append("\n## Reopen reason\n")
        parts.append(reopen_file.read_text(encoding="utf-8").strip())

    prior = _prior_outputs(workitem)
    if prior:
        header = "\n## Prior step outputs\n"
        if workitem.state.fix_iterations > 0:
            header += (
                f"> Fix iteration {workitem.state.fix_iterations}. "
                "Showing most recent output per role only "
                "(earlier rounds omitted).\n\n"
            )
        parts.append(header)
        for name, text in prior:
            parts.append(f"### {name}\n\n{text.rstrip()}\n")

    parts.append(
        "\n## Your task\n"
        f"Act as the **{role}** and produce your work product now."
    )
    return "\n".join(parts) + "\n"
