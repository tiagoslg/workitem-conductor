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

from pydantic import BaseModel, Field, field_validator

from .yaml_utils import coerce_str_list, extract_fenced_yaml

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


class ReviewDetails(BaseModel):
    """Optional structured detail a reviewer may add after its ``REVIEW:`` line.

    Purely informational this pass — it doesn't change the gate's
    approved/changes_requested behavior, which stays driven by
    ``parse_review_verdict`` alone. ``suggested_next_role`` in particular is
    recorded/displayed but does not yet override the flow's own
    ``on_changes`` loop-back target.
    """

    confidence: float | None = None
    blocking_issues: list[str] = Field(default_factory=list)
    non_blocking_issues: list[str] = Field(default_factory=list)
    suggested_next_role: str | None = None

    @field_validator("blocking_issues", "non_blocking_issues", mode="before")
    @classmethod
    def _coerce_lists(cls, v: object) -> object:
        return coerce_str_list(v)


def parse_review_details(text: str) -> ReviewDetails | None:
    """Return the reviewer's optional structured detail block, or ``None``.

    Only looks *after* the ``REVIEW:`` marker line — parsing the whole text
    would let the marker line itself misparse as a spurious one-key YAML
    mapping (``REVIEW: approved`` is valid YAML for ``{"REVIEW": "approved"}``).
    """
    text = text or ""
    m = _VERDICT_RE.search(text)
    if m is None:
        return None
    data = extract_fenced_yaml(text[m.end():], valid=lambda d: isinstance(d, dict))
    if data is None:
        return None
    return ReviewDetails.model_validate(data)
