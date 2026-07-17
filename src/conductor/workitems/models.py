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
from pydantic import BaseModel, Field, field_validator

from ..core.yaml_utils import coerce_str_list

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
    target_projects: list[str] = Field(default_factory=list)
    approved: bool = False

    @field_validator(
        "acceptance_criteria", "constraints", "validation",
        "stop_conditions", "target_projects",
        mode="before",
    )
    @classmethod
    def _coerce_str_list(cls, v: object) -> object:
        return coerce_str_list(v)

    def to_yaml(self) -> str:
        return _dump_yaml(self.model_dump())

    @classmethod
    def from_yaml(cls, text: str) -> "GoalContract":
        return cls.model_validate(yaml.safe_load(text) or {})


class HistoryEntry(BaseModel):
    at: str = Field(default_factory=utcnow_iso)
    summary: str


class StopReason(BaseModel):
    """Why a run stopped — shared by ``WorkitemState`` and ``RunRecord`` so
    both describe the same event the same way, not a display string that can
    drift from a structured field.

    ``type`` is a plain ``str``, not a strict ``Literal`` — same "don't crash
    on an unexpected value" posture already used for stage/status/next_action
    on ``WorkitemState``.
    """

    type: str = "other"
    message: str
    evidence: list[str] = Field(default_factory=list)


class WorkitemState(BaseModel):
    """Compact, evolvable execution state for a single workitem."""

    workitem_id: str
    title: str
    flow: str = "simple-change"
    strategy: str | None = None
    #: True once a human has explicitly pinned `strategy` via `--strategy` —
    #: `approve`'s automatic reselection skips a locked workitem.
    strategy_locked: bool = False
    stage: str = "defined"
    status: str = "draft"
    next_action: str = "approve_goal"
    step_index: int = 0
    iterations: int = 0
    fix_iterations: int = 0
    reopen_count: int = 0
    #: descriptive only — meaningful during/after a phased-flow run
    #: (`Flow.phase_flow` set); 0/0 for non-phased flows.
    current_phase_index: int = 0
    total_phases: int = 0
    feature_branch: str | None = None
    stop_reason: StopReason | None = None
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


class Decision(BaseModel):
    """A recorded decision — most often written by the summarizer role."""

    at: str = Field(default_factory=utcnow_iso)
    by: str = "summarizer"
    decision: str


class ValidationStatus(BaseModel):
    last_tests: list[str] = Field(default_factory=list)
    failing: list[str] = Field(default_factory=list)


class MemoryRecord(BaseModel):
    """Curated, human/LLM-written state of a workitem — the antidote to
    resending every prior raw output on each reopen (see ``core/context.py``).

    ``current_summary`` and ``open_issues`` represent *current* state and are
    replaced wholesale on each update; ``decisions``/``resolved_issues`` are
    an append-only history (see ``core/summarize.py::merge_memory``).
    """

    current_summary: str = ""
    decisions: list[Decision] = Field(default_factory=list)
    open_issues: list[str] = Field(default_factory=list)
    resolved_issues: list[str] = Field(default_factory=list)
    validation_status: ValidationStatus = Field(default_factory=ValidationStatus)

    def to_yaml(self) -> str:
        return _dump_yaml(self.model_dump())

    @classmethod
    def from_yaml(cls, text: str) -> "MemoryRecord":
        return cls.model_validate(yaml.safe_load(text) or {})
