"""Stop conditions: when the conductor must stop and hand back to the human.

The default posture is safe — stop rather than improvise beyond the approved
goal. This module holds the *deterministic* conditions the engine enforces
today. Semantic conditions (scope change, secrets/prod access required,
reviewer/implementer deadlock, high-risk surfaces) need analysis of provider
output and are deferred to a later slice; they are listed here as the intended
set so the engine has one place to grow.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Absolute ceiling on provider calls per run, independent of fix iterations.
#: A backstop against an unforeseen loop; normal runs end far below this.
GLOBAL_STEP_CAP = 50


@dataclass
class StopDecision:
    stop: bool
    reason: str = ""
    #: terminal status to record when stopping
    status: str = "needs_human"

    @classmethod
    def go(cls) -> "StopDecision":
        return cls(stop=False)


def check_max_fix_iterations(fix_iterations: int, max_fix: int) -> StopDecision:
    """Reviewer keeps requesting changes after the allowed number of fixes."""
    if fix_iterations >= max_fix:
        return StopDecision(
            stop=True,
            reason=f"reviewer still requesting changes after {max_fix} fix iteration(s)",
            status="needs_human",
        )
    return StopDecision.go()


def check_global_cap(iterations: int, cap: int = GLOBAL_STEP_CAP) -> StopDecision:
    """Backstop against runaway loops."""
    if iterations >= cap:
        return StopDecision(
            stop=True,
            reason=f"reached the global step cap of {cap}",
            status="needs_human",
        )
    return StopDecision.go()
