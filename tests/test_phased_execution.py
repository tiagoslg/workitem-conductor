"""Tests for phased execution (M8) — `Flow.phase_flow`, walked once per
planner phase, opt-in via the `phased-change` flow only."""

from __future__ import annotations

from pathlib import Path

import pytest

from conductor.core.engine import Engine
from conductor.flows.loader import load_flow
from conductor.paths import AiPaths
from conductor.providers.base import ProviderResult
from conductor.providers.dryrun import DryRunProvider
from conductor.scaffold import scaffold_ai
from conductor.workitems.manager import approve_goal, create_workitem, load_workitem

_PLANNER_TWO_PHASES = (
    "```yaml\n"
    "branch: feat/phased-thing\n"
    "phases:\n"
    "  - name: phase-one\n"
    "    goal: do the first part\n"
    "  - name: phase-two\n"
    "    goal: do the second part\n"
    "risk_level: medium\n"
    "```\n## Plan\nSee phases above.\n"
)


@pytest.fixture
def paths(tmp_path: Path) -> AiPaths:
    root = tmp_path / ".ai"
    scaffold_ai(root)
    return AiPaths(root=root)


class ScriptedPhasedProvider(DryRunProvider):
    """Configurable scripted provider for phased-execution tests.

    - planner always returns ``planner_output``.
    - reviewer consults ``review_script`` (a dict of phase_name -> list of
      verdicts, scripted per phase, consumed one at a time).
    - verifier consults ``verify_script`` (a list of verdicts, consumed once
      per call across the whole run).
    - implementer just returns generic dry-run-shaped text.
    """

    name = "scripted-phased"

    def __init__(self, planner_output: str, review_script=None, verify_script=None):
        self.planner_output = planner_output
        self.review_script = {k: list(v) for k, v in (review_script or {}).items()}
        self.verify_script = list(verify_script or [])
        self.calls: list[str] = []

    def run(self, request):
        self.calls.append(request.role)
        if request.role == "planner":
            return ProviderResult(ok=True, output=self.planner_output, provider=self.name)
        if request.role == "reviewer":
            # infer current phase from the "## Current phase" section injected by build_context
            phase_name = _extract_phase_name(request.prompt)
            script = self.review_script.get(phase_name, [])
            verdict = script.pop(0) if script else "approved"
            return ProviderResult(ok=True, output=f"REVIEW: {verdict}\n", provider=self.name)
        if request.role == "verifier":
            verdict = self.verify_script.pop(0) if self.verify_script else "passed"
            return ProviderResult(ok=True, output=f"VERIFY: {verdict}\n", provider=self.name)
        return ProviderResult(ok=True, output=f"# {request.role}\noutput", provider=self.name)


def _extract_phase_name(prompt: str) -> str | None:
    for line in prompt.splitlines():
        if line.strip().startswith("- name:"):
            return line.split(":", 1)[1].strip()
    return None


def test_phased_flow_runs_implementer_reviewer_once_per_phase(paths: AiPaths):
    wi = create_workitem(paths, "phased happy path")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "phased-change")
    provider = ScriptedPhasedProvider(_PLANNER_TWO_PHASES)

    engine = Engine(paths, flow, provider_for=lambda role: provider)
    outcome = engine.run(wi.workitem_id)

    assert outcome.completed is True
    roles = [s.role for s in outcome.steps]
    assert roles == [
        "planner",
        "implementer", "reviewer",   # phase-one
        "implementer", "reviewer",   # phase-two
        "verifier",
    ]
    phase_names = [s.phase_name for s in outcome.steps]
    assert phase_names == [None, "phase-one", "phase-one", "phase-two", "phase-two", None]

    reloaded = load_workitem(paths, wi.workitem_id)
    assert reloaded.state.total_phases == 2
    assert reloaded.state.current_phase_index == 1
    assert reloaded.state.status == "completed"


def test_review_loop_back_stays_within_its_own_phase(paths: AiPaths):
    wi = create_workitem(paths, "phase-scoped fix loop")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "phased-change")
    provider = ScriptedPhasedProvider(
        _PLANNER_TWO_PHASES,
        review_script={"phase-one": ["changes_requested"]},
    )

    engine = Engine(paths, flow, provider_for=lambda role: provider)
    outcome = engine.run(wi.workitem_id)

    assert outcome.completed is True
    roles_and_phases = [(s.role, s.phase_name) for s in outcome.steps]
    assert roles_and_phases == [
        ("planner", None),
        ("implementer", "phase-one"), ("reviewer", "phase-one"),
        ("implementer", "phase-one"), ("reviewer", "phase-one"),  # loop-back, still phase-one
        ("implementer", "phase-two"), ("reviewer", "phase-two"),
        ("verifier", None),
    ]
    reloaded = load_workitem(paths, wi.workitem_id)
    assert reloaded.state.fix_iterations == 1


def test_fix_budget_is_shared_across_phases(paths: AiPaths):
    wi = create_workitem(paths, "shared budget across phases")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "phased-change")  # max_fix_iterations = 3
    provider = ScriptedPhasedProvider(
        _PLANNER_TWO_PHASES,
        review_script={
            "phase-one": ["changes_requested"],
            "phase-two": ["changes_requested"],
        },
    )

    engine = Engine(paths, flow, provider_for=lambda role: provider)
    outcome = engine.run(wi.workitem_id)

    assert outcome.completed is True
    reloaded = load_workitem(paths, wi.workitem_id)
    # one loop-back in each of the two phases -> one shared counter, not two
    assert reloaded.state.fix_iterations == 2


def test_verifier_failure_redoes_only_the_last_phase(paths: AiPaths):
    wi = create_workitem(paths, "verifier redoes last phase")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "phased-change")
    provider = ScriptedPhasedProvider(_PLANNER_TWO_PHASES, verify_script=["failed", "passed"])

    engine = Engine(paths, flow, provider_for=lambda role: provider)
    outcome = engine.run(wi.workitem_id)

    assert outcome.completed is True
    roles_and_phases = [(s.role, s.phase_name) for s in outcome.steps]
    assert roles_and_phases == [
        ("planner", None),
        ("implementer", "phase-one"), ("reviewer", "phase-one"),
        ("implementer", "phase-two"), ("reviewer", "phase-two"),
        ("verifier", None),                                       # failed
        ("implementer", "phase-two"), ("reviewer", "phase-two"),   # redo last phase only
        ("verifier", None),                                        # passed
    ]
    reloaded = load_workitem(paths, wi.workitem_id)
    assert reloaded.state.fix_iterations == 1


def test_empty_phases_falls_back_to_running_phase_flow_once(paths: AiPaths):
    wi = create_workitem(paths, "planner forgot phases")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "phased-change")
    planner_output = "```yaml\nbranch: feat/no-phases\nphases: []\n```\n## Plan\n"
    provider = ScriptedPhasedProvider(planner_output)

    engine = Engine(paths, flow, provider_for=lambda role: provider)
    outcome = engine.run(wi.workitem_id)

    assert outcome.completed is True
    roles = [s.role for s in outcome.steps]
    assert roles == ["planner", "implementer", "reviewer", "verifier"]
    reloaded = load_workitem(paths, wi.workitem_id)
    assert reloaded.state.total_phases == 1


def test_non_phased_flow_is_unaffected(paths: AiPaths):
    """simple-change has no phase_flow — phases in the planner's own output
    (if any) must have zero effect on control flow."""
    wi = create_workitem(paths, "not a phased flow")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")
    provider = ScriptedPhasedProvider(_PLANNER_TWO_PHASES)

    engine = Engine(paths, flow, provider_for=lambda role: provider)
    outcome = engine.run(wi.workitem_id)

    assert outcome.completed is True
    roles = [s.role for s in outcome.steps]
    assert roles == ["planner", "implementer", "reviewer", "verifier"]
    reloaded = load_workitem(paths, wi.workitem_id)
    assert reloaded.state.total_phases == 0
