"""``PlanFrontmatter`` — the machine-readable index of an execution plan.

Schema v1, as agreed in ``docs/audit-layer-pivot.md`` §5. The plan's body
stays free-form prose (Background/Goal/Tasks/Acceptance criteria/Risks) —
only this frontmatter is parsed and validated by the conductor.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

Status = Literal["draft", "ready", "in_progress", "blocked", "done", "canceled"]


def _coerce_date(v: object) -> str | None:
    """YAML parses a bare ``2026-07-17`` as a ``datetime.date``, not a string."""
    if v is None:
        return None
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    return str(v)


class PlanFrontmatter(BaseModel):
    """Parsed frontmatter of one ``execution_plans/*.md`` file."""

    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    id: str
    sprint: str | None = None
    primary_repo: str
    branch: str | None = None
    status: Status = "draft"
    executable: bool = True
    commits: list[str] = []
    depends_on: list[str] = []
    related: list[str] = []
    blocked_until: str | None = None
    owner_team: str | None = None
    external_handoff: bool = False
    created_at: str | None = None
    completed_at: str | None = None

    @field_validator("created_at", "completed_at", "blocked_until", "sprint", "owner_team", mode="before")
    @classmethod
    def _coerce_optional_date_like(cls, v: object) -> object:
        if isinstance(v, (_dt.date, _dt.datetime)):
            return _coerce_date(v)
        return v


class Plan(BaseModel):
    """A plan located on disk, in a known repo."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    frontmatter: PlanFrontmatter
    repo: str
    path: Path

    @property
    def id(self) -> str:
        return self.frontmatter.id
