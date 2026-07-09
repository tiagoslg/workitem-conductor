"""Stop conditions: when the conductor must stop and hand back to the human.

The default posture is safe — stop rather than improvise beyond the approved
goal. This module holds both the *deterministic* backstops
(``check_global_cap``/``check_max_fix_iterations``) and the *semantic*
signal a role can raise itself (``parse_stop_signal``), which requires
judgement a deterministic rule can't make: scope expansion, secrets access, a
dangerous command, or production access. Deterministic repetitive-loop /
reviewer-implementer-deadlock detection is deferred — ``max_fix_iterations``
is the only backstop for a stuck fix loop for now.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..workitems.models import StopReason

#: Absolute ceiling on provider calls per run, independent of fix iterations.
#: A backstop against an unforeseen loop; normal runs end far below this.
GLOBAL_STEP_CAP = 50

#: Stop types that reflect a technical/infra failure rather than a semantic
#: or policy decision — these alone map to status "blocked".
_TECHNICAL_TYPES = {"provider_error"}


def status_for(stop_type: str) -> str:
    """The terminal ``WorkitemState.status`` for a given stop reason type."""
    return "blocked" if stop_type in _TECHNICAL_TYPES else "needs_human"


@dataclass
class StopDecision:
    stop: bool
    stop_reason: StopReason | None = None

    @property
    def reason(self) -> str:
        """Back-compat plain-string reason (``core/workspace_engine.py`` still
        reads this — kept working without touching that module)."""
        return self.stop_reason.message if self.stop_reason else ""

    @property
    def status(self) -> str:
        """Back-compat terminal status (see ``.reason``)."""
        return status_for(self.stop_reason.type) if self.stop_reason else "needs_human"

    @classmethod
    def go(cls) -> "StopDecision":
        return cls(stop=False)


def check_max_fix_iterations(fix_iterations: int, max_fix: int) -> StopDecision:
    """Reviewer keeps requesting changes after the allowed number of fixes."""
    if fix_iterations >= max_fix:
        return StopDecision(
            stop=True,
            stop_reason=StopReason(
                type="fix_loop_exhausted",
                message=f"reviewer still requesting changes after {max_fix} fix iteration(s)",
            ),
        )
    return StopDecision.go()


def check_global_cap(iterations: int, cap: int = GLOBAL_STEP_CAP) -> StopDecision:
    """Backstop against runaway loops."""
    if iterations >= cap:
        return StopDecision(
            stop=True,
            stop_reason=StopReason(
                type="global_cap",
                message=f"reached the global step cap of {cap}",
            ),
        )
    return StopDecision.go()


_STOP_RE = re.compile(
    r"(?im)^\s*STOP:\s*(scope_change|secrets_access|dangerous_command|production_access)\b[ \t]*(.*)$"
)


def parse_stop_signal(output: str) -> StopReason | None:
    """A role-declared safety stop, or ``None`` if no ``STOP:`` marker is present.

    Same leniency posture as ``core/review.py::parse_review_verdict``: an
    unmatched marker means "no stop signal", never an invented one.
    """
    m = _STOP_RE.search(output or "")
    if not m:
        return None
    stop_type = m.group(1).lower()
    same_line = m.group(2).strip()
    lines = [same_line] if same_line else []
    lines += [line.strip() for line in output[m.end():].splitlines() if line.strip()]
    evidence = [line[2:].strip() for line in lines if line.startswith(("- ", "* "))]
    message_lines = [line for line in lines if not line.startswith(("- ", "* "))]
    message = " ".join(message_lines) if message_lines else f"{stop_type} flagged by role"
    return StopReason(type=stop_type, message=message[:1000], evidence=evidence[:10])
