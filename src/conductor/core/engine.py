"""The execution engine: the conductor's ownership of the loop.

Given an approved workitem and a flow, the engine walks the steps in order: it
builds context for the step's role, calls the resolved provider, captures the
output as an artifact, advances the workitem state, and repeats — then writes a
final report. This slice runs the loop linearly (no fix/review back-edge yet);
the fix loop and stop conditions arrive in the next slice.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..flows.models import Flow
from ..paths import AiPaths
from ..providers.base import Provider, ProviderRequest
from ..workitems.manager import Workitem, load_workitem, save_state
from ..workitems.models import utcnow_iso
from .context import build_context

#: Resolves the provider to use for a given role. The registry (MVP 2 slice 2)
#: will build this from repo.yml; for now the CLI passes a constant.
ProviderFor = Callable[[str], Provider]


class GoalNotApproved(Exception):
    def __init__(self, workitem_id: str) -> None:
        super().__init__(
            f"Workitem '{workitem_id}' has not been approved. "
            f"Run `conductor approve` first."
        )


@dataclass
class StepOutcome:
    index: int
    role: str
    stage: str
    provider: str
    ok: bool
    prompt_path: Path
    output_path: Path
    error: str | None = None


@dataclass
class RunOutcome:
    workitem_id: str
    steps: list[StepOutcome] = field(default_factory=list)
    completed: bool = False
    stopped_reason: str | None = None


class Engine:
    def __init__(self, paths: AiPaths, flow: Flow, provider_for: ProviderFor) -> None:
        self.paths = paths
        self.flow = flow
        self.provider_for = provider_for

    def run(
        self,
        workitem_id: str,
        on_step: Callable[[StepOutcome], None] | None = None,
    ) -> RunOutcome:
        wi = load_workitem(self.paths, workitem_id)
        if not wi.goal.approved:
            raise GoalNotApproved(workitem_id)

        state = wi.state
        outputs_dir = wi.directory / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        outcome = RunOutcome(workitem_id=workitem_id)

        state.status = "running"
        save_state(self.paths, state)

        while state.step_index < len(self.flow.steps):
            step = self.flow.steps[state.step_index]
            idx = state.step_index
            provider = self.provider_for(step.role)

            prompt = build_context(self.paths, wi, step.role)
            request = ProviderRequest(
                role=step.role,
                prompt=prompt,
                workitem_id=workitem_id,
                cwd=self.paths.root.parent,
            )
            result = provider.run(request)

            prompt_path = outputs_dir / f"{idx:02d}-{step.role}.prompt.md"
            output_path = outputs_dir / f"{idx:02d}-{step.role}.output.md"
            prompt_path.write_text(prompt, encoding="utf-8")
            output_path.write_text(result.output, encoding="utf-8")

            step_outcome = StepOutcome(
                index=idx,
                role=step.role,
                stage=step.stage,
                provider=result.provider,
                ok=result.ok,
                prompt_path=prompt_path,
                output_path=output_path,
                error=result.error,
            )
            outcome.steps.append(step_outcome)

            rel_output = output_path.relative_to(wi.directory).as_posix()
            state.artifacts[step.role] = rel_output
            state.stage = step.stage
            state.iterations += 1

            if not result.ok:
                state.status = "blocked"
                state.stage = "blocked"
                state.next_action = "none"
                state.record(
                    f"{step.role} failed via {result.provider}: {result.error or 'unknown error'}"
                )
                save_state(self.paths, state)
                outcome.stopped_reason = f"provider failed at step '{step.role}'"
                if on_step:
                    on_step(step_outcome)
                return outcome

            state.step_index += 1
            state.record(f"{step.role} completed via {result.provider}")
            save_state(self.paths, state)
            if on_step:
                on_step(step_outcome)

        self._finish(wi, outcome)
        return outcome

    def _finish(self, wi: Workitem, outcome: RunOutcome) -> None:
        state = wi.state
        outcome.completed = True
        report = _build_final_report(wi, self.flow, outcome)
        report_path = wi.directory / "final_report.md"
        report_path.write_text(report, encoding="utf-8")

        state.artifacts["final_report"] = "final_report.md"
        state.stage = "completed"
        state.status = "completed"
        state.next_action = "none"
        state.record("flow completed; final report ready for human review")
        save_state(self.paths, state)


def _build_final_report(wi: Workitem, flow: Flow, outcome: RunOutcome) -> str:
    lines = [
        f"# Final report — {wi.workitem_id}",
        "",
        f"- title: {wi.state.title}",
        f"- flow: {flow.name}",
        f"- generated: {utcnow_iso()}",
        f"- status: {'completed' if outcome.completed else 'incomplete'}",
        "",
        "## Goal",
        "",
        "```yaml",
        wi.goal.to_yaml().rstrip(),
        "```",
        "",
        "## Steps",
        "",
    ]
    for step in outcome.steps:
        mark = "✓" if step.ok else "✗"
        rel = step.output_path.relative_to(wi.directory).as_posix()
        lines.append(f"- {mark} **{step.role}** ({step.stage}) via {step.provider} — `{rel}`")
    if outcome.stopped_reason:
        lines += ["", f"> Stopped: {outcome.stopped_reason}"]
    lines += [
        "",
        "## Human validation",
        "",
        "Review the step outputs above against the goal's acceptance criteria,",
        "then accept or reopen this workitem.",
        "",
    ]
    return "\n".join(lines)
