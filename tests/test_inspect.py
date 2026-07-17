"""Tests for `conductor inspect` — goal/state summary + run history."""

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conductor.cli import app
from conductor.paths import AiPaths
from conductor.scaffold import scaffold_ai
from conductor.workitems.manager import approve_goal, create_workitem, reopen_workitem, save_memory
from conductor.workitems.models import MemoryRecord, ValidationStatus

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


def test_inspect_shows_resolved_issues_and_validation_status(paths: AiPaths, monkeypatch):
    monkeypatch.chdir(paths.root.parent)
    wi = create_workitem(paths, "memory display")
    save_memory(paths, wi.workitem_id, MemoryRecord(
        resolved_issues=["fixed the flaky login test"],
        validation_status=ValidationStatus(failing=["test_logout"]),
    ))
    result = runner.invoke(app, ["inspect"])
    assert result.exit_code == 0
    assert "fixed the flaky login test" in result.output
    assert "test_logout" in result.output


def test_inspect_shows_stop_reason(git_paths: AiPaths, monkeypatch):
    paths = git_paths
    monkeypatch.chdir(paths.root.parent)
    wi = create_workitem(paths, "will stop")
    approve_goal(paths, wi.workitem_id)

    paths.repo_config.write_text(
        "name: test\n"
        "providers:\n"
        "  stopper: { type: cli_one_shot, command: sh, "
        "args: [\"-c\", \"echo 'STOP: scope_change'; echo 'needs a bigger scope'; echo '- migrate the DB'\"], "
        "prompt_via: stdin }\n"
        "roles:\n"
        "  planner: { provider: stopper }\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["execute"])
    assert result.exit_code == 1

    inspect_result = runner.invoke(app, ["inspect"])
    assert inspect_result.exit_code == 0
    assert "scope_change" in inspect_result.output
    assert "needs a bigger scope" in inspect_result.output
    assert "migrate the DB" in inspect_result.output


def test_inspect_shows_phase_progress(git_paths: AiPaths, monkeypatch):
    paths = git_paths
    monkeypatch.chdir(paths.root.parent)
    wi = create_workitem(paths, "phased docs change")
    approve_goal(paths, wi.workitem_id)

    planner_yaml = (
        "```yaml\\nbranch: feat/x\\nphases:\\n  - name: p1\\n  - name: p2\\n```\\n"
    )
    paths.repo_config.write_text(
        "name: test\n"
        "providers:\n"
        "  dry: { type: dry_run }\n"
        f"  planner_cli: {{ type: cli_one_shot, command: sh, args: [\"-c\", \"printf '{planner_yaml}'\"], prompt_via: stdin }}\n"
        "roles:\n"
        "  planner: { provider: planner_cli }\n"
        "  implementer: { provider: dry }\n"
        "  reviewer: { provider: dry }\n"
        "  verifier: { provider: dry }\n",
        encoding="utf-8",
    )

    assert runner.invoke(app, ["approve", "--strategy", "phased-documentation"]).exit_code == 0
    result = runner.invoke(app, ["execute"])
    assert result.exit_code == 0

    inspect_result = runner.invoke(app, ["inspect"])
    assert inspect_result.exit_code == 0
    assert "phase" in inspect_result.output.lower()
    assert "2/2" in inspect_result.output


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


def test_inspect_shows_structured_verify_details(git_paths: AiPaths, monkeypatch):
    paths = git_paths
    monkeypatch.chdir(paths.root.parent)
    wi = create_workitem(paths, "verifier reports structured detail")
    approve_goal(paths, wi.workitem_id)

    paths.repo_config.write_text(
        "name: test\n"
        "providers:\n"
        "  dry: { type: dry_run }\n"
        "  verifier_cli: { type: cli_one_shot, command: sh, args: [\"-c\", "
        "\"printf 'VERIFY: passed\\n\\x60\\x60\\x60yaml\\ntests_run: true\\nnotes:\\n  - all good\\n\\x60\\x60\\x60\\n'\"], "
        "prompt_via: stdin }\n"
        "roles:\n"
        "  planner: { provider: dry }\n"
        "  implementer: { provider: dry }\n"
        "  reviewer: { provider: dry }\n"
        "  verifier: { provider: verifier_cli }\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["execute"])
    assert result.exit_code == 0

    inspect_result = runner.invoke(app, ["inspect"])
    assert inspect_result.exit_code == 0
    assert "tests_run=True" in inspect_result.output
    assert "all good" in inspect_result.output


def test_inspect_shows_active_strategy(git_paths: AiPaths, monkeypatch):
    paths = git_paths
    monkeypatch.chdir(paths.root.parent)
    assert runner.invoke(app, ["define", "fix a small bug", "--strategy", "bugfix"]).exit_code == 0

    result = runner.invoke(app, ["inspect"])
    assert result.exit_code == 0
    assert "strategy" in result.output.lower()
    assert "bugfix" in result.output


def test_inspect_workspace_shows_run_with_project_steps(tmp_path: Path, monkeypatch):
    """A workspace workitem's `inspect -w` shows run history with per-project steps,
    now that WorkspaceEngine writes run.yml/metrics.yml (fast-follow to M4)."""
    from conductor.workitems.manager import load_workitem, save_goal

    project_a = tmp_path / "project-a"
    project_a.mkdir()
    _git_init(project_a)
    scaffold_ai(project_a / ".ai")

    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["workspace", "add", str(project_a), "-w", "test-ws"]).exit_code == 0
    assert runner.invoke(app, ["define", "-w", "test-ws", "cross-project change"]).exit_code == 0

    from conductor.workspaces import load_workspace_paths
    ws_paths = load_workspace_paths("test-ws")
    wid = ws_paths.active_pointer.read_text(encoding="utf-8").strip()
    wi = load_workitem(ws_paths, wid)
    wi.goal.target_projects = ["project-a"]
    save_goal(ws_paths, wid, wi.goal)

    assert runner.invoke(app, ["approve", "-w", "test-ws"]).exit_code == 0
    assert runner.invoke(app, ["execute", "-w", "test-ws", "--dry-run"]).exit_code == 0

    result = runner.invoke(app, ["inspect", "-w", "test-ws"])
    assert result.exit_code == 0
    assert "run-001" in result.output
    assert "project-a" in result.output
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
