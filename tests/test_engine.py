from pathlib import Path

import pytest

from conductor.core.engine import Engine, GoalNotApproved
from conductor.flows.loader import FlowNotFound, load_flow
from conductor.paths import AiPaths
from conductor.providers.base import ProviderRequest, ProviderResult
from conductor.providers.dryrun import DryRunProvider
from conductor.scaffold import scaffold_ai
from conductor.workitems.manager import approve_goal, create_workitem, load_workitem


@pytest.fixture
def paths(tmp_path: Path) -> AiPaths:
    root = tmp_path / ".ai"
    scaffold_ai(root)
    return AiPaths(root=root)


def test_load_flow(paths: AiPaths):
    flow = load_flow(paths, "simple-change")
    assert flow.name == "simple-change"
    roles = [s.role for s in flow.steps]
    assert roles == ["planner", "implementer", "reviewer", "validator"]
    assert flow.max_fix_iterations == 3


def test_load_flow_missing(paths: AiPaths):
    with pytest.raises(FlowNotFound):
        load_flow(paths, "no-such-flow")


def test_dryrun_provider_echoes_prompt():
    provider = DryRunProvider()
    result = provider.run(
        ProviderRequest(role="planner", prompt="PROMPT-BODY", workitem_id="wi", cwd=Path("."))
    )
    assert isinstance(result, ProviderResult)
    assert result.ok
    assert result.provider == "dry_run"
    assert "PROMPT-BODY" in result.output


def test_engine_runs_full_flow(paths: AiPaths):
    wi = create_workitem(paths, "do the thing")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")
    provider = DryRunProvider()

    engine = Engine(paths, flow, provider_for=lambda role: provider)
    seen = []
    outcome = engine.run(wi.workitem_id, on_step=lambda s: seen.append(s.role))

    assert outcome.completed is True
    assert [s.role for s in outcome.steps] == ["planner", "implementer", "reviewer", "validator"]
    assert seen == ["planner", "implementer", "reviewer", "validator"]

    # artifacts written
    out_dir = wi.directory / "outputs"
    assert (out_dir / "00-planner.output.md").is_file()
    assert (out_dir / "00-planner.prompt.md").is_file()
    assert (out_dir / "03-validator.output.md").is_file()
    assert (wi.directory / "final_report.md").is_file()

    # state advanced + persisted
    reloaded = load_workitem(paths, wi.workitem_id)
    assert reloaded.state.status == "completed"
    assert reloaded.state.stage == "completed"
    assert reloaded.state.next_action == "none"
    assert reloaded.state.step_index == 4
    assert reloaded.state.artifacts["validator"] == "outputs/03-validator.output.md"
    assert reloaded.state.artifacts["final_report"] == "final_report.md"

    report = (wi.directory / "final_report.md").read_text()
    assert "status: completed" in report
    assert "incomplete" not in report


def test_engine_requires_approval(paths: AiPaths):
    wi = create_workitem(paths, "not approved")
    flow = load_flow(paths, "simple-change")
    engine = Engine(paths, flow, provider_for=lambda role: DryRunProvider())
    with pytest.raises(GoalNotApproved):
        engine.run(wi.workitem_id)


def test_engine_later_step_sees_prior_output(paths: AiPaths):
    wi = create_workitem(paths, "context flows forward")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")
    engine = Engine(paths, flow, provider_for=lambda role: DryRunProvider())
    engine.run(wi.workitem_id)

    # the implementer's prompt should embed the planner's prior output
    impl_prompt = (wi.directory / "outputs" / "01-implementer.prompt.md").read_text()
    assert "Prior step outputs" in impl_prompt
    assert "00-planner.output.md" in impl_prompt


def test_engine_stops_on_provider_failure(paths: AiPaths):
    wi = create_workitem(paths, "failing provider")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")

    class FailingProvider(DryRunProvider):
        name = "failing"

        def run(self, request):
            return ProviderResult(ok=False, output="", provider=self.name, error="boom")

    engine = Engine(paths, flow, provider_for=lambda role: FailingProvider())
    outcome = engine.run(wi.workitem_id)

    assert outcome.completed is False
    assert "planner" in outcome.stopped_reason
    reloaded = load_workitem(paths, wi.workitem_id)
    assert reloaded.state.status == "blocked"
    assert reloaded.state.step_index == 0  # did not advance past the failed step
