"""Models for flow definitions loaded from ``.ai/flows/<name>.yml``.

A flow is just an ordered list of steps; each step names a role and the stage
the workitem enters while that role runs. Keeping flows declarative (role names,
not providers) is what lets the same flow run on different backends.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FlowStep(BaseModel):
    role: str
    stage: str
    #: when set to "review", the engine parses the step's output for a verdict
    #: (approved / changes_requested) and may loop back.
    gate: str | None = None
    #: on a "changes_requested" verdict, the role to loop back to (e.g. implementer).
    on_changes: str | None = None


class Flow(BaseModel):
    name: str
    description: str = ""
    steps: list[FlowStep] = Field(default_factory=list)
    max_fix_iterations: int = 3

    def step_for_role(self, role: str) -> FlowStep | None:
        for step in self.steps:
            if step.role == role:
                return step
        return None

    def index_of_role(self, role: str) -> int | None:
        for i, step in enumerate(self.steps):
            if step.role == role:
                return i
        return None
