"""Rule-based strategy selection.

Only 2 of the backlog's 4 documented rules are reachable this pass:

    if target_projects > 1        -> cross-project-change   # workspace-only; WorkspaceEngine
                                                              # doesn't call this selector yet
    if reopen_count >= 2          -> context-heavy-change    # reopen doesn't re-run the
                                                              # selector yet (no approve step)
    if acceptance_criteria has "docs" -> phased-documentation
    else                          -> simple-change

The first two are specified in the backlog but have no call site that could
ever trigger them this pass (documented in ``docs/backlog-adjusted.md`` as a
deferred fast-follow, not silently dropped) — implementing them here would be
dead code.
"""

from __future__ import annotations

from ..workitems.models import GoalContract, WorkitemState


def select_strategy(goal: GoalContract, state: WorkitemState) -> str:
    text = " ".join(goal.acceptance_criteria).lower()
    if "docs" in text or "documentation" in text:
        return "phased-documentation"
    return "simple-change"
