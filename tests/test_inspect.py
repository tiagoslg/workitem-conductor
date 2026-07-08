"""Tests for `conductor inspect` — goal/state summary + run history."""

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conductor.cli import app
from conductor.paths import AiPaths
from conductor.scaffold import scaffold_ai
from conductor.workitems.manager import approve_goal, create_workitem, reopen_workitem

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
    """Same as ``paths`` but with a real git repo, needed for `execute` to create a worktree."""
    _git_init(paths.root.parent)
    return paths


def test_inspect_no_workitem(paths: AiPaths, monkeypatch):
    monkeypatch.chdir(paths.root.parent)
    result = runner.invoke(app, ["inspect"])
    assert result.exit_code == 1
    assert "No workitem to inspect" in result.output


def test_inspect_before_any_run(paths: AiPaths, monkeypatch):
    monkeypatch.chdir(paths.root.parent)
    create_workitem(paths, "not executed yet")
    result = runner.invoke(app, ["inspect"])
    assert result.exit_code == 0
    assert "No runs yet" in result.output


def test_inspect_active_flag_matches_default(paths: AiPaths, monkeypatch):
    monkeypatch.chdir(paths.root.parent)
    wi = create_workitem(paths, "explicit --active")
    default_result = runner.invoke(app, ["inspect"])
    active_result = runner.invoke(app, ["inspect", "--active"])
    assert default_result.exit_code == active_result.exit_code == 0
    assert wi.workitem_id in default_result.output
    assert wi.workitem_id in active_result.output


def test_inspect_rejects_id_and_active_together(paths: AiPaths, monkeypatch):
    monkeypatch.chdir(paths.root.parent)
    wi = create_workitem(paths, "conflicting flags")
    result = runner.invoke(app, ["inspect", wi.workitem_id, "--active"])
    assert result.exit_code == 1
    assert "either" in result.output.lower()


def test_inspect_shows_latest_run(git_paths: AiPaths, monkeypatch):
    paths = git_paths
    monkeypatch.chdir(paths.root.parent)
    wi = create_workitem(paths, "inspect me")
    approve_goal(paths, wi.workitem_id)

    exec_result = runner.invoke(app, ["execute", "--dry-run"])
    assert exec_result.exit_code == 0

    result = runner.invoke(app, ["inspect"])
    assert result.exit_code == 0
    assert "run-001" in result.output
    assert "planner" in result.output


def test_inspect_runs_flag_lists_all_runs(git_paths: AiPaths, monkeypatch):
    paths = git_paths
    monkeypatch.chdir(paths.root.parent)
    wi = create_workitem(paths, "iterate")
    approve_goal(paths, wi.workitem_id)

    assert runner.invoke(app, ["execute", "--dry-run"]).exit_code == 0
    reopen_workitem(paths, wi.workitem_id, "one more pass")
    assert runner.invoke(app, ["execute", "--dry-run"]).exit_code == 0

    default_result = runner.invoke(app, ["inspect"])
    assert "run-001" not in default_result.output
    assert "run-002" in default_result.output

    all_runs_result = runner.invoke(app, ["inspect", "--runs"])
    assert "run-001" in all_runs_result.output
    assert "run-002" in all_runs_result.output
