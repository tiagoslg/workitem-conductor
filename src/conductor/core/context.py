"""Assemble the prompt/context handed to a role for one step.

Context is built selectively rather than by replaying full transcripts: the role
instructions, the goal contract, and the work products of prior steps. This is
where token strategy lives — for now we include prior outputs verbatim; later
steps can summarise them.
"""

from __future__ import annotations

from ..paths import AiPaths
from ..workitems.manager import Workitem

_FALLBACK_ROLE_PROMPT = (
    "# Role: {role}\n\n"
    "You are the **{role}** for this workitem. Act within the approved goal and "
    "scope, produce a clear work product, and flag anything that should stop and "
    "ask the human.\n"
)


def load_role_prompt(paths: AiPaths, role: str) -> str:
    """Return the role's instructions from ``.ai/roles/<role>.md``.

    Falls back to a generic instruction when no prompt file exists, so custom
    roles in a flow don't require a prompt file to be runnable.
    """
    role_file = paths.roles_dir / f"{role}.md"
    if role_file.is_file():
        return role_file.read_text(encoding="utf-8")
    return _FALLBACK_ROLE_PROMPT.format(role=role)


def _prior_outputs(workitem: Workitem) -> list[tuple[str, str]]:
    """(filename, text) for each prior step output, in run order."""
    outputs_dir = workitem.directory / "outputs"
    if not outputs_dir.is_dir():
        return []
    results = []
    for path in sorted(outputs_dir.glob("*.output.md")):
        results.append((path.name, path.read_text(encoding="utf-8")))
    return results


def build_context(paths: AiPaths, workitem: Workitem, role: str) -> str:
    """Compose the full prompt text for ``role`` on ``workitem``."""
    parts: list[str] = []
    parts.append(load_role_prompt(paths, role).rstrip())

    parts.append("\n---\n## Workitem\n")
    parts.append(f"- id: {workitem.workitem_id}")
    parts.append(f"- title: {workitem.state.title}")

    parts.append("\n## Goal contract\n")
    parts.append("```yaml\n" + workitem.goal.to_yaml().rstrip() + "\n```")

    prior = _prior_outputs(workitem)
    if prior:
        parts.append("\n## Prior step outputs\n")
        for name, text in prior:
            parts.append(f"### {name}\n\n{text.rstrip()}\n")

    parts.append(
        "\n## Your task\n"
        f"Act as the **{role}** and produce your work product now."
    )
    return "\n".join(parts) + "\n"
