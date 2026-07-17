"""Parse the planner's structured plan from its output.

The planner emits a fenced YAML block (no bare ``BRANCH:`` line convention
any more — this replaced it): ``branch``, an ordered ``phases`` list (each
with a ``name``/``goal``/``files_likely_touched``), and a ``risk_level``.
``STOP:`` still takes priority and is checked separately, before this parser
ever runs.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from .yaml_utils import coerce_str_list, extract_fenced_yaml


class PlannerPhase(BaseModel):
    name: str
    goal: str = ""
    files_likely_touched: list[str] = Field(default_factory=list)

    @field_validator("files_likely_touched", mode="before")
    @classmethod
    def _coerce_files(cls, v: object) -> object:
        return coerce_str_list(v)


class PlannerPlan(BaseModel):
    branch: str | None = None
    phases: list[PlannerPhase] = Field(default_factory=list)
    risk_level: str | None = None


def parse_planner_output(text: str) -> PlannerPlan | None:
    """Return the planner's structured plan, or ``None`` if no valid YAML block is present."""
    data = extract_fenced_yaml(text or "", valid=lambda d: isinstance(d, dict))
    if data is None:
        return None
    return PlannerPlan.model_validate(data)
