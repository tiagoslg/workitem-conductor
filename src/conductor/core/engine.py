"""The execution engine: the conductor's ownership of the loop.

Given an approved workitem and a flow, the engine walks the steps in order: it
builds context for the step's role, calls the resolved provider, captures the
output as an artifact, advances the workitem state, and repeats — then writes a
final report. A review step can send the loop back to the implementer (the fix
loop) until the reviewer approves or a stop condition is hit.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..flows.models import Flow, FlowStep
from ..paths import AiPaths
from ..providers.base import Provider, ProviderRequest
from ..workitems.manager import Workitem, load_workitem, save_state
from ..workitems.models import WorkitemState, utcnow_iso
from . import stop_conditions
from .context import build_context
from .review import parse_review_verdict

#: Resolves the provider to use for a given role. Built from repo.yml by the
#: registry; the engine depends only on this callable, not on any backend.
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
    #: for review-gated steps: "approved" | "changes_requested" | "unknown"
    verdict: str | None = None
    #: True when this step's verdict sent the loop back for a fix
    looped_back: bool = False


@dataclass
class RunOutcome:
    workitem_id: str
    steps: list[StepOutcome] = field(default_factory=list)
    completed: bool = False
    stopped_reason: str | None = None


class Engine:
    def __init__(
        self,
        paths: AiPaths,
        flow: Flow,
        provider_for: ProviderFor,
        execution_cwd: Path | None = None,
    ) -> None:
        self.paths = paths
        self.flow = flow
        self.provider_for = provider_for
        self._execution_cwd = execution_cwd or paths.cwd

    def run(
        self,
        workitem_id: str,
        on_step: Callable[[StepOutcome], None] | None = None,
        on_step_start: Callable[[str, str], None] | None = None,
        on_step_output: Callable[[str], None] | None = None,
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
            # Backstop against a runaway loop, independent of fix iterations.
            cap = stop_conditions.check_global_cap(state.iterations)
            if cap.stop:
                self._stop(wi, outcome, cap.reason, cap.status)
                return outcome

            step = self.flow.steps[state.step_index]
            seq = state.iterations  # global, monotonic — preserves fix history
            provider = self.provider_for(step.role)

            prompt = build_context(self.paths, wi, step.role)
            prompt_path = outputs_dir / f"{seq:02d}-{step.role}.prompt.md"
            output_path = outputs_dir / f"{seq:02d}-{step.role}.output.md"
            prompt_path.write_text(prompt, encoding="utf-8")
            if on_step_start:
                on_step_start(step.role, provider.name)
            result = provider.run(
                ProviderRequest(
                    role=step.role,
                    prompt=prompt,
                    workitem_id=workitem_id,
                    cwd=self._execution_cwd,
                    on_output=on_step_output,
                )
            )
            output_path.write_text(result.output, encoding="utf-8")

            step_outcome = StepOutcome(
                index=seq,
                role=step.role,
                stage=step.stage,
                provider=result.provider,
                ok=result.ok,
                prompt_path=prompt_path,
                output_path=output_path,
                error=result.error,
            )
            outcome.steps.append(step_outcome)

            state.artifacts[step.role] = output_path.relative_to(wi.directory).as_posix()
            state.stage = step.stage
            state.iterations += 1

            if not result.ok:
                self._stop(
                    wi,
                    outcome,
                    f"provider failed at step '{step.role}': "
                    f"{result.error or 'unknown error'}",
                    status="blocked",
                )
                if on_step:
                    on_step(step_outcome)
                return outcome

            # Review gate: decide whether to advance or loop back to fix.
            if step.gate == "review":
                verdict = parse_review_verdict(result.output)
                step_outcome.verdict = verdict
                if verdict == "changes_requested":
                    decision = stop_conditions.check_max_fix_iterations(
                        state.fix_iterations, self.flow.max_fix_iterations
                    )
                    if decision.stop:
                        state.open_issues.append(decision.reason)
                        self._stop(wi, outcome, decision.reason, decision.status)
                        if on_step:
                            on_step(step_outcome)
                        return outcome
                    self._loop_back(state, step, step_outcome)
                    save_state(self.paths, state)
                    if on_step:
                        on_step(step_outcome)
                    continue

            if step.role == "planner" and result.ok:
                m = re.search(r"^BRANCH:\s*(\S+)", result.output, re.MULTILINE)
                if m:
                    state.feature_branch = m.group(1).strip()

            state.step_index += 1
            state.record(f"{step.role} completed via {result.provider}")
            save_state(self.paths, state)
            if on_step:
                on_step(step_outcome)

        self._finish(wi, outcome)
        return outcome

    def _loop_back(self, state: WorkitemState, step: FlowStep, outcome: StepOutcome) -> None:
        """Send the loop back to the step's ``on_changes`` role for a fix pass."""
        target_role = step.on_changes
        target_index = self.flow.index_of_role(target_role) if target_role else None
        if target_index is None:
            # Misconfigured gate: fall back to re-running from the start of the flow.
            target_index = 0
        state.fix_iterations += 1
        state.step_index = target_index
        state.stage = "fixing"
        outcome.looped_back = True
        state.record(
            f"reviewer requested changes; looping back to '{target_role or 'start'}' "
            f"(fix {state.fix_iterations}/{self.flow.max_fix_iterations})"
        )

    def _stop(
        self, wi: Workitem, outcome: RunOutcome, reason: str, status: str
    ) -> None:
        """Record a terminal stop state and reason."""
        state = wi.state
        state.status = status
        state.stage = "blocked"
        state.next_action = "none"
        state.record(f"stopped: {reason}")
        save_state(self.paths, state)
        outcome.stopped_reason = reason

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
        suffix = ""
        if step.verdict and step.verdict != "unknown":
            suffix = f" → _{step.verdict}_"
            if step.looped_back:
                suffix += " (looped back to fix)"
        lines.append(
            f"- {mark} **{step.role}** ({step.stage}) via {step.provider} — `{rel}`{suffix}"
        )
    if wi.state.fix_iterations:
        lines += ["", f"_Fix iterations: {wi.state.fix_iterations}._"]
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
