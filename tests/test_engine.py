from pathlib import Path

import pytest

from conductor.config.models import ContextConfig
from conductor.core.engine import Engine, GoalNotApproved
from conductor.flows.loader import FlowNotFound, load_flow
from conductor.paths import AiPaths
from conductor.providers.base import ProviderRequest, ProviderResult
from conductor.providers.dryrun import DryRunProvider
from conductor.scaffold import scaffold_ai
from conductor.workitems.manager import approve_goal, create_workitem, load_memory, load_workitem


@pytest.fixture
def paths(tmp_path: Path) -> AiPaths:
    root = tmp_path / ".ai"
    scaffold_ai(root)
    return AiPaths(root=root)


def test_load_flow(paths: AiPaths):
    flow = load_flow(paths, "simple-change")
    assert flow.name == "simple-change"
    roles = [s.role for s in flow.steps]
    assert roles == ["planner", "implementer", "reviewer", "verifier"]
    assert flow.max_fix_iterations == 3


def test_load_flow_missing(paths: AiPaths):
    with pytest.raises(FlowNotFound):
        load_flow(paths, "no-such-flow")


def test_dryrun_provider_returns_placeholder():
    provider = DryRunProvider()
    result = provider.run(
        ProviderRequest(role="planner", prompt="PROMPT-BODY", workitem_id="wi", cwd=Path("."))
    )
    assert isinstance(result, ProviderResult)
    assert result.ok
    assert result.provider == "dry_run"
    assert "planner" in result.output
    # must not echo the prompt verbatim (would confuse the review gate)
    assert "PROMPT-BODY" not in result.output


def test_engine_runs_full_flow(paths: AiPaths):
    wi = create_workitem(paths, "do the thing")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")
    provider = DryRunProvider()

    engine = Engine(paths, flow, provider_for=lambda role: provider)
    seen = []
    outcome = engine.run(wi.workitem_id, on_step=lambda s: seen.append(s.role))

    assert outcome.completed is True
    assert [s.role for s in outcome.steps] == ["planner", "implementer", "reviewer", "verifier"]
    assert seen == ["planner", "implementer", "reviewer", "verifier"]

    # artifacts written
    out_dir = wi.directory / "outputs"
    assert (out_dir / "00-planner.output.md").is_file()
    assert (out_dir / "00-planner.prompt.md").is_file()
    assert (out_dir / "03-verifier.output.md").is_file()
    assert (wi.directory / "final_report.md").is_file()

    # state advanced + persisted
    reloaded = load_workitem(paths, wi.workitem_id)
    assert reloaded.state.status == "completed"
    assert reloaded.state.stage == "completed"
    assert reloaded.state.next_action == "none"
    assert reloaded.state.step_index == 4
    assert reloaded.state.artifacts["verifier"] == "outputs/03-verifier.output.md"
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
    engine = Engine(
        paths, flow, provider_for=lambda role: DryRunProvider(),
        context_config=ContextConfig(include_raw_outputs=True),
    )
    engine.run(wi.workitem_id)

    # the implementer's prompt should embed the planner's prior output
    impl_prompt = (wi.directory / "outputs" / "01-implementer.prompt.md").read_text()
    assert "Prior step outputs" in impl_prompt
    assert "00-planner.output.md" in impl_prompt


class ScriptedReviewer(DryRunProvider):
    """Provider whose reviewer emits a scripted sequence of verdicts."""

    name = "scripted"

    def __init__(self, verdicts):
        self.verdicts = verdicts
        self.review_calls = 0

    def run(self, request):
        if request.role == "reviewer":
            verdict = self.verdicts[min(self.review_calls, len(self.verdicts) - 1)]
            self.review_calls += 1
            return ProviderResult(
                ok=True,
                output=f"review body\nREVIEW: {verdict}\n",
                provider=self.name,
            )
        return ProviderResult(ok=True, output=f"# {request.role}\noutput", provider=self.name)


def test_fix_loop_resolves_after_changes(paths: AiPaths):
    wi = create_workitem(paths, "fix loop")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")
    reviewer = ScriptedReviewer(["changes_requested", "changes_requested", "approved"])

    engine = Engine(paths, flow, provider_for=lambda role: reviewer)
    outcome = engine.run(wi.workitem_id)

    assert outcome.completed is True
    roles = [s.role for s in outcome.steps]
    assert roles == [
        "planner", "implementer", "reviewer",
        "implementer", "reviewer",
        "implementer", "reviewer",
        "verifier",
    ]

    reloaded = load_workitem(paths, wi.workitem_id)
    assert reloaded.state.fix_iterations == 2
    assert reloaded.state.status == "completed"
    # outputs are sequence-numbered, preserving the fix history
    out_dir = wi.directory / "outputs"
    assert (out_dir / "03-implementer.output.md").is_file()
    assert (out_dir / "06-reviewer.output.md").is_file()


def test_fix_loop_stops_at_max_iterations(paths: AiPaths):
    wi = create_workitem(paths, "endless changes")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")  # max_fix_iterations = 3
    reviewer = ScriptedReviewer(["changes_requested"])  # never approves

    engine = Engine(paths, flow, provider_for=lambda role: reviewer)
    outcome = engine.run(wi.workitem_id)

    assert outcome.completed is False
    assert "fix iteration" in outcome.stopped_reason.message

    reloaded = load_workitem(paths, wi.workitem_id)
    assert reloaded.state.status == "needs_human"
    assert reloaded.state.stage == "blocked"
    assert reloaded.state.fix_iterations == 3
    assert any("fix iteration" in i for i in reloaded.state.open_issues)


def test_dry_run_passes_review_gate(paths: AiPaths):
    # dry-run reviewer emits no verdict -> unknown -> treated as approved
    wi = create_workitem(paths, "dry passes gate")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")
    engine = Engine(paths, flow, provider_for=lambda role: DryRunProvider())
    outcome = engine.run(wi.workitem_id)
    assert outcome.completed is True
    assert load_workitem(paths, wi.workitem_id).state.fix_iterations == 0


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
    assert "planner" in outcome.stopped_reason.message
    reloaded = load_workitem(paths, wi.workitem_id)
    assert reloaded.state.status == "blocked"
    assert reloaded.state.step_index == 0  # did not advance past the failed step


class ScriptedRoleOutputProvider(DryRunProvider):
    """Provider that returns a fixed output for one role, dry-run for the rest."""

    name = "scripted-role"

    def __init__(self, role: str, output: str) -> None:
        self.role = role
        self.output = output

    def run(self, request):
        if request.role == self.role:
            return ProviderResult(ok=True, output=self.output, provider=self.name)
        return ProviderResult(ok=True, output=f"# {request.role}\noutput", provider=self.name)


def test_stop_marker_from_planner_halts_run_before_implementer(paths: AiPaths):
    wi = create_workitem(paths, "risky change")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")
    provider = ScriptedRoleOutputProvider(
        "planner",
        "STOP: scope_change\nThis needs a schema migration outside approved scope.\n",
    )

    engine = Engine(paths, flow, provider_for=lambda role: provider)
    outcome = engine.run(wi.workitem_id)

    assert outcome.completed is False
    assert [s.role for s in outcome.steps] == ["planner"]
    assert outcome.stopped_reason.type == "scope_change"
    assert "schema migration" in outcome.stopped_reason.message

    reloaded = load_workitem(paths, wi.workitem_id)
    assert reloaded.state.status == "needs_human"
    assert reloaded.state.stop_reason.type == "scope_change"


def test_stop_marker_from_implementer_halts_before_reviewer(paths: AiPaths):
    wi = create_workitem(paths, "needs secrets")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")
    provider = ScriptedRoleOutputProvider(
        "implementer",
        "STOP: secrets_access\nRequires reading .env.production to proceed.\n",
    )

    engine = Engine(paths, flow, provider_for=lambda role: provider)
    outcome = engine.run(wi.workitem_id)

    assert outcome.completed is False
    assert [s.role for s in outcome.steps] == ["planner", "implementer"]
    assert outcome.stopped_reason.type == "secrets_access"


def test_stop_marker_takes_precedence_over_review_verdict(paths: AiPaths):
    wi = create_workitem(paths, "conflicting signals")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")
    provider = ScriptedRoleOutputProvider(
        "reviewer",
        "STOP: dangerous_command\nWould require `rm -rf /data`.\nREVIEW: approved\n",
    )

    engine = Engine(paths, flow, provider_for=lambda role: provider)
    outcome = engine.run(wi.workitem_id)

    assert outcome.completed is False
    assert outcome.stopped_reason.type == "dangerous_command"
    reloaded = load_workitem(paths, wi.workitem_id)
    assert reloaded.state.status == "needs_human"


def test_reopen_clears_prior_stop_reason(paths: AiPaths):
    from conductor.workitems.manager import reopen_workitem

    wi = create_workitem(paths, "will be stopped then reopened")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")
    provider = ScriptedRoleOutputProvider(
        "planner", "STOP: scope_change\nout of scope.\n"
    )
    engine = Engine(paths, flow, provider_for=lambda role: provider)
    engine.run(wi.workitem_id)

    assert load_workitem(paths, wi.workitem_id).state.stop_reason is not None

    reopen_workitem(paths, wi.workitem_id, "try again")
    assert load_workitem(paths, wi.workitem_id).state.stop_reason is None


# --- planner structured output reaches state.feature_branch (see also
# tests/test_planner_output.py for the parser itself) ---

def test_planner_yaml_plan_sets_feature_branch(paths: AiPaths):
    wi = create_workitem(paths, "plan sets branch")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")
    provider = ScriptedRoleOutputProvider(
        "planner",
        "```yaml\nbranch: feat/add-slugify\nphases:\n  - name: p1\n```\n## Plan\nDo it.\n",
    )
    engine = Engine(paths, flow, provider_for=lambda role: provider)
    engine.run(wi.workitem_id)

    reloaded = load_workitem(paths, wi.workitem_id)
    assert reloaded.state.feature_branch == "feat/add-slugify"


def test_planner_output_without_yaml_leaves_branch_unset(paths: AiPaths):
    wi = create_workitem(paths, "plan without branch")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")
    provider = ScriptedRoleOutputProvider("planner", "## Plan\nJust prose, no YAML block.\n")
    engine = Engine(paths, flow, provider_for=lambda role: provider)
    engine.run(wi.workitem_id)

    reloaded = load_workitem(paths, wi.workitem_id)
    assert reloaded.state.feature_branch is None


# --- verify gate (verifier role) ---

class ScriptedVerifier(DryRunProvider):
    """Provider whose verifier emits a scripted sequence of verdicts; all
    other roles behave like dry-run."""

    name = "scripted-verifier"

    def __init__(self, verdicts):
        self.verdicts = verdicts
        self.verify_calls = 0

    def run(self, request):
        if request.role == "verifier":
            verdict = self.verdicts[min(self.verify_calls, len(self.verdicts) - 1)]
            self.verify_calls += 1
            return ProviderResult(
                ok=True,
                output=f"verify body\nVERIFY: {verdict}\n",
                provider=self.name,
            )
        return ProviderResult(ok=True, output=f"# {request.role}\noutput", provider=self.name)


def test_verify_gate_loops_back_to_implementer_then_passes(paths: AiPaths):
    wi = create_workitem(paths, "verify fails then passes")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")
    verifier = ScriptedVerifier(["failed", "passed"])

    engine = Engine(paths, flow, provider_for=lambda role: verifier)
    outcome = engine.run(wi.workitem_id)

    assert outcome.completed is True
    roles = [s.role for s in outcome.steps]
    assert roles == [
        "planner", "implementer", "reviewer", "verifier",
        "implementer", "reviewer", "verifier",
    ]
    reloaded = load_workitem(paths, wi.workitem_id)
    assert reloaded.state.fix_iterations == 1
    assert reloaded.state.status == "completed"


def test_verify_gate_shares_fix_budget_with_review_gate(paths: AiPaths):
    """A prior review loop-back and a verify loop-back share one fix_iterations
    counter, not two separate budgets."""
    wi = create_workitem(paths, "review then verify both loop back")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")  # max_fix_iterations = 3

    class Scripted(DryRunProvider):
        name = "scripted-both"

        def __init__(self):
            self.review_calls = 0
            self.verify_calls = 0

        def run(self, request):
            if request.role == "reviewer":
                verdict = "changes_requested" if self.review_calls == 0 else "approved"
                self.review_calls += 1
                return ProviderResult(ok=True, output=f"REVIEW: {verdict}\n", provider=self.name)
            if request.role == "verifier":
                verdict = "failed" if self.verify_calls == 0 else "passed"
                self.verify_calls += 1
                return ProviderResult(ok=True, output=f"VERIFY: {verdict}\n", provider=self.name)
            return ProviderResult(ok=True, output=f"# {request.role}\noutput", provider=self.name)

    provider = Scripted()
    engine = Engine(paths, flow, provider_for=lambda role: provider)
    outcome = engine.run(wi.workitem_id)

    assert outcome.completed is True
    reloaded = load_workitem(paths, wi.workitem_id)
    assert reloaded.state.fix_iterations == 2


def test_verify_gate_unknown_treated_as_passed(paths: AiPaths):
    # dry-run verifier emits no VERIFY: marker -> unknown -> treated as passed
    wi = create_workitem(paths, "dry passes verify gate")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")
    engine = Engine(paths, flow, provider_for=lambda role: DryRunProvider())
    outcome = engine.run(wi.workitem_id)
    assert outcome.completed is True
    assert load_workitem(paths, wi.workitem_id).state.fix_iterations == 0


def test_verify_gate_exhausts_max_fix_iterations(paths: AiPaths):
    wi = create_workitem(paths, "verify never passes")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")  # max_fix_iterations = 3
    verifier = ScriptedVerifier(["failed"])  # never passes

    engine = Engine(paths, flow, provider_for=lambda role: verifier)
    outcome = engine.run(wi.workitem_id)

    assert outcome.completed is False
    assert outcome.stopped_reason.type == "fix_loop_exhausted"
    reloaded = load_workitem(paths, wi.workitem_id)
    assert reloaded.state.status == "needs_human"
    assert reloaded.state.fix_iterations == 3


# --- summarizer trigger points ---

class _CountingSummaryProvider(DryRunProvider):
    name = "counting_summarizer"

    def __init__(self):
        self.calls = 0

    def run(self, request):
        self.calls += 1
        return ProviderResult(
            ok=True,
            output=f"SUMMARY:\n```yaml\ncurrent_summary: call {self.calls}\n```\n",
            provider=self.name,
        )


def test_summarizer_called_on_finish(paths: AiPaths):
    wi = create_workitem(paths, "summarize on finish")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")
    summarizer = _CountingSummaryProvider()

    def provider_for(role):
        return summarizer if role == "summarizer" else DryRunProvider()

    engine = Engine(paths, flow, provider_for=provider_for)
    outcome = engine.run(wi.workitem_id)

    assert outcome.completed is True
    assert summarizer.calls == 1
    assert load_memory(paths, wi.workitem_id).current_summary == "call 1"


def test_summarizer_called_on_stop(paths: AiPaths):
    wi = create_workitem(paths, "summarize on stop")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")
    summarizer = _CountingSummaryProvider()

    class FailingProvider(DryRunProvider):
        def run(self, request):
            return ProviderResult(ok=False, output="", provider="failing", error="boom")

    def provider_for(role):
        return summarizer if role == "summarizer" else FailingProvider()

    engine = Engine(paths, flow, provider_for=provider_for)
    outcome = engine.run(wi.workitem_id)

    assert outcome.completed is False
    assert summarizer.calls == 1
    assert load_memory(paths, wi.workitem_id).current_summary == "call 1"


def test_summarizer_called_on_each_loop_back(paths: AiPaths):
    wi = create_workitem(paths, "summarize on loop back")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")
    reviewer = ScriptedReviewer(["changes_requested", "changes_requested", "approved"])
    summarizer = _CountingSummaryProvider()

    def provider_for(role):
        return summarizer if role == "summarizer" else reviewer

    engine = Engine(paths, flow, provider_for=provider_for)
    outcome = engine.run(wi.workitem_id)

    assert outcome.completed is True
    # 2 loop-backs (changes_requested x2) + 1 final finish
    assert summarizer.calls == 3


def test_summarizer_failure_does_not_break_run(paths: AiPaths):
    """A broken summarizer binding must not prevent the run from completing."""
    wi = create_workitem(paths, "summarizer explodes")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")

    class ExplodingSummarizer(DryRunProvider):
        def run(self, request):
            raise RuntimeError("summarizer provider is broken")

    def provider_for(role):
        return ExplodingSummarizer() if role == "summarizer" else DryRunProvider()

    engine = Engine(paths, flow, provider_for=provider_for)
    outcome = engine.run(wi.workitem_id)

    assert outcome.completed is True
    reloaded = load_workitem(paths, wi.workitem_id)
    assert any("summarizer skipped" in h.summary for h in reloaded.state.history)
