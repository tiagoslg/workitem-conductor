"""Tests for `--strategy` on `define`/`approve` and its lock/reselect behavior (M7)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conductor.cli import app
from conductor.core.runs import list_run_ids, load_run
from conductor.paths import AiPaths
from conductor.scaffold import scaffold_ai
from conductor.workitems.manager import get_active_id, load_workitem

runner = CliRunner()


def _git_init(repo_root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_root, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-q", "-m", "init"], cwd=repo_root, check=True)


@pytest.fixture
def paths(tmp_path: Path) -> AiPaths:
    root = tmp_path / ".ai"
    scaffold_ai(root)
    return AiPaths(root=root)


@pytest.fixture
def git_paths(paths: AiPaths) -> AiPaths:
    _git_init(paths.root.parent)
    return paths


def test_define_without_strategy_selects_simple_change(paths: AiPaths, monkeypatch):
    monkeypatch.chdir(paths.root.parent)
    result = runner.invoke(app, ["define", "fix a small bug"])
    assert result.exit_code == 0

    wid = get_active_id(paths)
    wi = load_workitem(paths, wid)
    assert wi.state.strategy == "simple-change"
    assert wi.state.strategy_locked is False


def test_define_with_strategy_locks_it(paths: AiPaths, monkeypatch):
    monkeypatch.chdir(paths.root.parent)
    result = runner.invoke(app, ["define", "fix a small bug", "--strategy", "bugfix"])
    assert result.exit_code == 0

    wid = get_active_id(paths)
    wi = load_workitem(paths, wid)
    assert wi.state.strategy == "bugfix"
    assert wi.state.strategy_locked is True


def test_define_with_unknown_strategy_fails_cleanly(paths: AiPaths, monkeypatch):
    monkeypatch.chdir(paths.root.parent)
    result = runner.invoke(app, ["define", "fix a small bug", "--strategy", "no-such-strategy"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_approve_reselects_using_refined_goal(paths: AiPaths, monkeypatch):
    monkeypatch.chdir(paths.root.parent)
    runner.invoke(app, ["define", "improve the API"])
    wid = get_active_id(paths)

    from conductor.workitems.manager import save_goal

    wi = load_workitem(paths, wid)
    wi.goal.acceptance_criteria = ["Update the docs for the new endpoint"]
    save_goal(paths, wid, wi.goal)

    result = runner.invoke(app, ["approve"])
    assert result.exit_code == 0

    reloaded = load_workitem(paths, wid)
    assert reloaded.state.strategy == "phased-documentation"


def test_approve_does_not_clobber_a_locked_strategy(paths: AiPaths, monkeypatch):
    monkeypatch.chdir(paths.root.parent)
    runner.invoke(app, ["define", "improve the API", "--strategy", "bugfix"])
    wid = get_active_id(paths)

    from conductor.workitems.manager import save_goal

    wi = load_workitem(paths, wid)
    wi.goal.acceptance_criteria = ["Update the docs for the new endpoint"]
    save_goal(paths, wid, wi.goal)

    result = runner.invoke(app, ["approve"])
    assert result.exit_code == 0

    reloaded = load_workitem(paths, wid)
    assert reloaded.state.strategy == "bugfix"


def test_approve_with_strategy_overrides_and_locks(paths: AiPaths, monkeypatch):
    monkeypatch.chdir(paths.root.parent)
    runner.invoke(app, ["define", "improve the API"])
    wid = get_active_id(paths)

    result = runner.invoke(app, ["approve", "--strategy", "context-heavy-change"])
    assert result.exit_code == 0

    reloaded = load_workitem(paths, wid)
    assert reloaded.state.strategy == "context-heavy-change"
    assert reloaded.state.strategy_locked is True


def test_strategy_role_override_changes_which_provider_runs(git_paths: AiPaths, monkeypatch):
    """A strategy's `roles` overlay picks a different provider for a role than
    repo.yml's own binding — proving build_provider_for's role_overrides
    actually takes effect end to end, not just in the registry unit test."""
    paths = git_paths
    monkeypatch.chdir(paths.root.parent)

    paths.repo_config.write_text(
        "name: test\n"
        "providers:\n"
        "  default_impl: { type: cli_one_shot, command: sh, args: [\"-c\", \"echo DEFAULT_PROVIDER\"], prompt_via: stdin }\n"
        "  override_impl: { type: cli_one_shot, command: sh, args: [\"-c\", \"echo OVERRIDE_PROVIDER\"], prompt_via: stdin }\n"
        "  dry: { type: dry_run }\n"
        "roles:\n"
        "  planner: { provider: dry }\n"
        "  implementer: { provider: default_impl }\n"
        "  reviewer: { provider: dry }\n"
        "  validator: { provider: dry }\n",
        encoding="utf-8",
    )
    (paths.strategies_dir / "override-impl.yml").write_text(
        "flow: simple-change\n"
        "roles:\n"
        "  implementer: { provider: override_impl }\n",
        encoding="utf-8",
    )

    runner.invoke(app, ["define", "swap the implementer provider"])
    wid = get_active_id(paths)
    assert runner.invoke(app, ["approve", "--strategy", "override-impl"]).exit_code == 0
    assert runner.invoke(app, ["execute"]).exit_code == 0

    wi = load_workitem(paths, wid)
    impl_output = (wi.directory / "outputs" / "01-implementer.output.md").read_text()
    assert "OVERRIDE_PROVIDER" in impl_output
    assert "DEFAULT_PROVIDER" not in impl_output

    run = load_run(wi.directory, "run-001")
    assert run.strategy == "override-impl"
    assert run.strategy_hash is not None
    impl_step = next(s for s in run.steps if s.role == "implementer")
    assert impl_step.provider == "override_impl"


def test_strategy_max_fix_iterations_overrides_flow_default(git_paths: AiPaths, monkeypatch):
    """A strategy's max_fix_iterations caps the fix loop tighter than the
    flow's own default (3), proving the override actually reaches Engine."""
    paths = git_paths
    monkeypatch.chdir(paths.root.parent)

    paths.repo_config.write_text(
        "name: test\n"
        "providers:\n"
        "  dry: { type: dry_run }\n"
        "  always_changes: { type: cli_one_shot, command: sh, args: [\"-c\", \"echo 'REVIEW: changes_requested'\"], prompt_via: stdin }\n"
        "roles:\n"
        "  planner: { provider: dry }\n"
        "  implementer: { provider: dry }\n"
        "  reviewer: { provider: always_changes }\n"
        "  validator: { provider: dry }\n",
        encoding="utf-8",
    )
    (paths.strategies_dir / "tight-fix-loop.yml").write_text(
        "flow: simple-change\nmax_fix_iterations: 1\n",
        encoding="utf-8",
    )

    runner.invoke(app, ["define", "a change that never gets approved"])
    wid = get_active_id(paths)
    assert runner.invoke(app, ["approve", "--strategy", "tight-fix-loop"]).exit_code == 0
    result = runner.invoke(app, ["execute"])
    assert result.exit_code == 1

    wi = load_workitem(paths, wid)
    assert wi.state.fix_iterations == 1
    assert wi.state.stop_reason is not None
    assert wi.state.stop_reason.type == "fix_loop_exhausted"
