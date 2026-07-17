"""Parse the verifier's verdict from its output.

Same convention as ``core/review.py``: a single explicit marker line drives
the gate deterministically —

    VERIFY: passed
    VERIFY: failed

If no such line is present the verdict is ``unknown`` and the engine treats
it as passed (same permissive posture as the reviewer's "unknown => approved"
— an unmatched marker means "no verdict", never an invented failure). An
optional fenced YAML block after the marker line adds structured detail
(tests run, acceptance criteria met, notes) that's informational only.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .yaml_utils import coerce_str_list, extract_fenced_yaml

_VERIFY_RE = re.compile(r"(?im)^\s*VERIFY:\s*(passed|failed)\b")


def parse_verify_verdict(text: str) -> Literal["passed", "failed", "unknown"]:
    """Return the last explicit ``VERIFY:`` verdict in ``text``, or ``unknown``."""
    matches = _VERIFY_RE.findall(text or "")
    if not matches:
        return "unknown"
    return matches[-1].lower()


class VerifyDetails(BaseModel):
    tests_run: bool | None = None
    tests_passed: bool | None = None
    acceptance_criteria_met: bool | None = None
    notes: list[str] = Field(default_factory=list)

    @field_validator("notes", mode="before")
    @classmethod
    def _coerce_notes(cls, v: object) -> object:
        return coerce_str_list(v)


def parse_verify_details(text: str) -> VerifyDetails | None:
    """Return the verifier's optional structured detail block, or ``None``.

    Only looks *after* the ``VERIFY:`` marker line — see
    ``core/review.py::parse_review_details`` for why parsing the whole text
    would risk misparsing the marker line itself as YAML.
    """
    text = text or ""
    m = _VERIFY_RE.search(text)
    if m is None:
        return None
    data = extract_fenced_yaml(text[m.end():], valid=lambda d: isinstance(d, dict))
    if data is None:
        return None
    return VerifyDetails.model_validate(data)
