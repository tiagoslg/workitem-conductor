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
