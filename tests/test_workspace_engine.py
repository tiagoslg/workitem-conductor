"""Tests for `WorkspaceEngine` — the two-phase cross-project execution loop.

Covers the fast-follow work that brings run/metrics recording, summarizer
calls, and STOP-marker safety detection to workspace runs, matching what the
single-repo `Engine` already had from M4/M5/M6.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conductor.core.runs import list_run_ids, load_metrics, load_run
from conductor.core.workspace_engine import WorkspaceEngine
from conductor.paths import WorkspacePaths
from conductor.providers.base import Provider, ProviderRequest, ProviderResult
from conductor.providers.dryrun import DryRunProvider
from conductor.scaffold import scaffold_ai, scaffold_workspace
from conductor.workitems.manager import (
    approve_goal,
    create_workitem,
    load_memory,
    load_workitem,
    save_goal,
)


def _git_init(repo_root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_root, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-q", "-m", "init"], cwd=repo_root, check=True)


@pytest.fixture
def ws_paths(tmp_path: Path) -> WorkspacePaths:
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    for project in (project_a, project_b):
        project.mkdir()
        _git_init(project)
        scaffold_ai(project / ".ai")
    root = tmp_path / "workspaces" / "test-ws"
    scaffold_workspace(root, "test-ws")
    return WorkspacePaths(root=root, name="test-ws", project_roots=[project_a, project_b])


def _make_workitem(ws_paths: WorkspacePaths, projects=("project-a", "project-b")) -> str:
    wi = create_workitem(ws_paths, "cross-project change", flow="workspace-analysis")
    wi.goal.target_projects = list(projects)
    save_goal(ws_paths, wi.workitem_id, wi.goal)
    approve_goal(ws_paths, wi.workitem_id)
    return wi.workitem_id


class ScriptedRoleOutputProvider(DryRunProvider):
    """Provider that returns a fixed output for one role, dry-run for the rest."""

    name = "scripted-role"

    def __init__(self, role: str, output: str) -> None:
        self.role = role
        self.output = output

    def run(self, request: ProviderRequest) -> ProviderResult:
        if request.role == self.role:
            return ProviderResult(ok=True, output=self.output, provider=self.name)
        return ProviderResult(ok=True, output=f"# {request.role}\noutput", provider=self.name)


class RaisingProvider(Provider):
    """Provider that always raises — for exercising the summarizer's never-break guarantee."""

    name = "raising"

    def run(self, request: ProviderRequest) -> ProviderResult:
        raise RuntimeError("boom")


def test_execute_writes_run_and_metrics_with_project_steps(ws_paths: WorkspacePaths):
    wid = _make_workitem(ws_paths)
    provider = DryRunProvider()
    engine = WorkspaceEngine(ws_paths, provider_for=lambda role: provider)

    outcome = engine.run(wid)
    assert outcome.completed is True

    wi = load_workitem(ws_paths, wid)
    assert list_run_ids(wi.directory) == ["run-001"]

    run = load_run(wi.directory, "run-001")
    assert run.status == "completed"
    assert run.flow == "workspace-change"
    assert run.stop_reason is None

    roles_and_projects = [(s.role, s.project_name) for s in run.steps]
    assert roles_and_projects[0] == ("planner", None)
    assert ("implementer", "project-a") in roles_and_projects
    assert ("reviewer", "project-a") in roles_and_projects
    assert ("implementer", "project-b") in roles_and_projects
    assert ("reviewer", "project-b") in roles_and_projects

    metrics = load_metrics(wi.directory, "run-001")
    assert metrics is not None
    assert metrics.git is None
    assert metrics.context["total_prompt_chars"] > 0
    assert metrics.providers["planner"] == "dry_run"
    assert metrics.providers["project-a:implementer"] == "dry_run"


def test_stop_marker_from_project_implementer_halts_entire_workspace_run(
    ws_paths: WorkspacePaths,
):
    wid = _make_workitem(ws_paths)
    provider = ScriptedRoleOutputProvider(
        "implementer", "STOP: dangerous_command\nWould require `rm -rf /data`.\n"
    )
    engine = WorkspaceEngine(ws_paths, provider_for=lambda role: provider)

    outcome = engine.run(wid)

    assert outcome.completed is False
    assert outcome.stopped_reason.type == "dangerous_command"
    assert "rm -rf /data" in outcome.stopped_reason.message

    # only the first project's implementer ran — the second project was never touched
    project_names = {s.project_name for s in outcome.project_steps}
    assert project_names == {"project-a"}
    assert [s.role for s in outcome.project_steps] == ["implementer"]

    wi = load_workitem(ws_paths, wid)
    assert wi.state.status == "needs_human"
    assert wi.state.stop_reason.type == "dangerous_command"

    run = load_run(wi.directory, "run-001")
    assert run.stop_reason.type == "dangerous_command"


def test_stop_marker_takes_precedence_over_reviewer_verdict(ws_paths: WorkspacePaths):
    wid = _make_workitem(ws_paths, projects=("project-a",))
    provider = ScriptedRoleOutputProvider(
        "reviewer", "STOP: scope_change\nNeeds broader scope.\nREVIEW: approved\n"
    )
    engine = WorkspaceEngine(ws_paths, provider_for=lambda role: provider)

    outcome = engine.run(wid)

    assert outcome.completed is False
    assert outcome.stopped_reason.type == "scope_change"
    rev_steps = [s for s in outcome.project_steps if s.role == "reviewer"]
    assert len(rev_steps) == 1
    assert rev_steps[0].verdict is None


def test_summarizer_called_once_and_updates_memory(ws_paths: WorkspacePaths):
    wid = _make_workitem(ws_paths, projects=("project-a",))
    summarizer = ScriptedRoleOutputProvider(
        "summarizer",
        "SUMMARY:\n```yaml\ncurrent_summary: workspace run complete\n```\n",
    )

    def provider_for(role: str) -> Provider:
        return summarizer if role == "summarizer" else DryRunProvider()

    engine = WorkspaceEngine(ws_paths, provider_for=provider_for)
    outcome = engine.run(wid)

    assert outcome.completed is True
    memory = load_memory(ws_paths, wid)
    assert memory.current_summary == "workspace run complete"


def test_summarizer_failure_does_not_break_run(ws_paths: WorkspacePaths):
    wid = _make_workitem(ws_paths, projects=("project-a",))

    def provider_for(role: str) -> Provider:
        return RaisingProvider() if role == "summarizer" else DryRunProvider()

    engine = WorkspaceEngine(ws_paths, provider_for=provider_for)
    outcome = engine.run(wid)

    assert outcome.completed is True
    wi = load_workitem(ws_paths, wid)
    assert any("summarizer skipped" in h.summary for h in wi.state.history)


def test_stop_marker_from_planner_halts_before_any_project_starts(ws_paths: WorkspacePaths):
    wid = _make_workitem(ws_paths)
    provider = ScriptedRoleOutputProvider(
        "planner", "STOP: scope_change\nThis needs projects outside target_projects.\n"
    )
    engine = WorkspaceEngine(ws_paths, provider_for=lambda role: provider)

    outcome = engine.run(wid)

    assert outcome.completed is False
    assert outcome.stopped_reason.type == "scope_change"
    assert outcome.project_steps == []  # no project was ever started

    wi = load_workitem(ws_paths, wid)
    assert wi.state.status == "needs_human"
    assert wi.state.stop_reason.type == "scope_change"

    run = load_run(wi.directory, "run-001")
    assert [s.role for s in run.steps] == ["planner"]
    assert run.stop_reason.type == "scope_change"


class FixLoopReviewerProvider(DryRunProvider):
    """Requests changes on the first reviewer call, approves on the second."""

    name = "fix-loop-reviewer"

    def __init__(self) -> None:
        self.reviewer_calls = 0

    def run(self, request: ProviderRequest) -> ProviderResult:
        if request.role != "reviewer":
            return ProviderResult(ok=True, output=f"# {request.role}\noutput", provider=self.name)
        self.reviewer_calls += 1
        verdict = "changes_requested" if self.reviewer_calls == 1 else "approved"
        return ProviderResult(ok=True, output=f"REVIEW: {verdict}\n", provider=self.name)


def test_fix_loop_in_workspace_project_records_both_rounds(ws_paths: WorkspacePaths):
    wid = _make_workitem(ws_paths, projects=("project-a",))
    provider = FixLoopReviewerProvider()
    engine = WorkspaceEngine(ws_paths, provider_for=lambda role: provider)

    outcome = engine.run(wid)

    assert outcome.completed is True
    assert provider.reviewer_calls == 2

    impl_steps = [s for s in outcome.project_steps if s.role == "implementer"]
    rev_steps = [s for s in outcome.project_steps if s.role == "reviewer"]
    assert len(impl_steps) == 2
    assert len(rev_steps) == 2
    assert rev_steps[0].verdict == "changes_requested"
    assert rev_steps[0].looped_back is True
    assert rev_steps[1].verdict == "approved"
    assert rev_steps[1].looped_back is False

    wi = load_workitem(ws_paths, wid)
    run = load_run(wi.directory, "run-001")
    assert [s.role for s in run.steps] == ["planner", "implementer", "reviewer", "implementer", "reviewer"]
    assert run.steps[2].looped_back is True
    assert run.steps[2].verdict == "changes_requested"
    assert run.steps[4].verdict == "approved"
    assert all(s.project_name in (None, "project-a") for s in run.steps)


def test_planner_failure_writes_run_with_provider_error_stop_reason(ws_paths: WorkspacePaths):
    wid = _make_workitem(ws_paths)

    class FailingPlannerProvider(DryRunProvider):
        def run(self, request: ProviderRequest) -> ProviderResult:
            if request.role == "planner":
                return ProviderResult(ok=False, output="", provider="dry_run", error="boom")
            return super().run(request)

    provider = FailingPlannerProvider()
    engine = WorkspaceEngine(ws_paths, provider_for=lambda role: provider)

    outcome = engine.run(wid)

    assert outcome.completed is False
    assert outcome.stopped_reason.type == "provider_error"

    wi = load_workitem(ws_paths, wid)
    run = load_run(wi.directory, "run-001")
    assert run.steps == [s for s in run.steps if s.role == "planner"]
    assert run.stop_reason.type == "provider_error"
