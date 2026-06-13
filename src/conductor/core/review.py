"""Parse a reviewer's verdict from its output.

The convention is a single explicit line so the decision is deterministic and
provider-agnostic:

    REVIEW: approved
    REVIEW: changes_requested

If no such line is present the verdict is ``unknown`` and the engine treats it
as approved (it does not invent blockers). The reviewer role prompt instructs
the model to emit the line; the dry-run provider never does, so dry-run flows
pass straight through the gate.
"""

from __future__ import annotations

import re

Verdict = str  # "approved" | "changes_requested" | "unknown"

_VERDICT_RE = re.compile(
    r"(?im)^\s*REVIEW:\s*(approved|changes_requested)\b"
)


def parse_review_verdict(text: str) -> Verdict:
    """Return the last explicit ``REVIEW:`` verdict in ``text``, or ``unknown``."""
    matches = _VERDICT_RE.findall(text or "")
    if not matches:
        return "unknown"
    return matches[-1].lower()
