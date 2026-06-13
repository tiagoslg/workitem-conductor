"""Pydantic models for the goal contract and workitem state.

These are intentionally small and forgiving. ``stage``/``status``/``next_action``
are typed as ``Literal`` for documentation and editor help, but the loaders keep
unknown values rather than rejecting hand-edited files — the conductor should
surface a confusing state, not crash on it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import yaml
from pydantic import BaseModel, Field

Stage = Literal[
    "defined",
    "planning",
    "implementing",
    "reviewing",
    "fixing",
    "validating",
    "completed",
    "blocked",
]

Status = Literal[
    "draft",
    "ready",
    "running",
    "needs_human",
    "blocked",
    "completed",
]

NextAction = Literal[
    "approve_goal",
    "execute",
    "plan",
    "implement",
    "review",
    "fix",
    "validate",
    "close",
    "none",
]


def utcnow_iso() -> str:
    """Current UTC time as an ISO-8601 string (seconds precision, ``Z`` suffix)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _dump_yaml(data: dict) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)


class Scope(BaseModel):
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class GoalContract(BaseModel):
    """The approved (or in-progress) statement of intent for a workitem."""

    goal: str
    scope: Scope = Field(default_factory=Scope)
    acceptance_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    validation: list[str] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=list)
    approved: bool = False

    def to_yaml(self) -> str:
        return _dump_yaml(self.model_dump())

    @classmethod
    def from_yaml(cls, text: str) -> "GoalContract":
        return cls.model_validate(yaml.safe_load(text) or {})


class HistoryEntry(BaseModel):
    at: str = Field(default_factory=utcnow_iso)
    summary: str


class WorkitemState(BaseModel):
    """Compact, evolvable execution state for a single workitem."""

    workitem_id: str
    title: str
    flow: str = "simple-change"
    stage: str = "defined"
    status: str = "draft"
    next_action: str = "approve_goal"
    iterations: int = 0
    open_issues: list[str] = Field(default_factory=list)
    human_overrides: list[str] = Field(default_factory=list)
    artifacts: dict[str, str | None] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utcnow_iso)
    updated_at: str = Field(default_factory=utcnow_iso)
    history: list[HistoryEntry] = Field(default_factory=list)

    def record(self, summary: str) -> None:
        """Append a history entry and bump ``updated_at``."""
        now = utcnow_iso()
        self.history.append(HistoryEntry(at=now, summary=summary))
        self.updated_at = now

    def to_yaml(self) -> str:
        return _dump_yaml(self.model_dump())

    @classmethod
    def from_yaml(cls, text: str) -> "WorkitemState":
        return cls.model_validate(yaml.safe_load(text) or {})
