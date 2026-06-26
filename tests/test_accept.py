"""Tests for `conductor accept` — git staging + commit + optional push."""

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from typer.testing import CliRunner

from conductor.cli import app
from conductor.paths import AiPaths
from conductor.scaffold import scaffold_ai
from conductor.workitems.manager import approve_goal, create_workitem


@pytest.fixture
def paths(tmp_path: Path) -> AiPaths:
    root = tmp_path / ".ai"
    scaffold_ai(root)
    return AiPaths(root=root)


@pytest.fixture
def approved_workitem(paths: AiPaths):
    wi = create_workitem(paths, "fix the null check in validator")
    approve_goal(paths, wi.workitem_id)
    return wi, paths


runner = CliRunner()


def _mock_run(returncode_map: dict[tuple, int], *, dirty: bool = True):
    """Build a subprocess.run mock.

    ``returncode_map`` maps the first element of ``args`` (the git subcommand)
    to a return code.  ``dirty=True`` makes the diff-check return 1 (changes
    staged), ``dirty=False`` simulates a clean tree.
    """
    def _run(args, **kwargs):
        result = MagicMock()
        result.stderr = ""
        result.stdout = ""
        if args[:2] == ["git", "diff"]:
            result.returncode = 0 if not dirty else 1
        else:
            subcmd = args[1]  # "add", "commit", "push"
            result.returncode = returncode_map.get(subcmd, 0)
        return result
    return _run


def test_accept_commits_with_goal_title(approved_workitem, monkeypatch):
    wi, paths = approved_workitem
    monkeypatch.chdir(paths.root.parent)

    committed_msgs = []

    def fake_run(args, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stderr = ""
        if args[:2] == ["git", "diff"]:
            r.returncode = 1  # dirty tree
        if args[1] == "commit":
            committed_msgs.append(args[args.index("-m") + 1])
        return r

    with patch("conductor.cli.subprocess.run", side_effect=fake_run):
        result = runner.invoke(app, ["accept"])

    assert result.exit_code == 0
    assert "Committed" in result.output
    assert committed_msgs[0].startswith("fix the null check in validator")
    assert "Workitem:" in committed_msgs[0]


def test_accept_custom_message(approved_workitem, monkeypatch):
    wi, paths = approved_workitem
    monkeypatch.chdir(paths.root.parent)

    committed_msgs = []

    def fake_run(args, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stderr = ""
        if args[:2] == ["git", "diff"]:
            r.returncode = 1
        if args[1] == "commit":
            committed_msgs.append(args[args.index("-m") + 1])
        return r

    with patch("conductor.cli.subprocess.run", side_effect=fake_run):
        result = runner.invoke(app, ["accept", "--message", "my custom message"])

    assert result.exit_code == 0
    assert committed_msgs == ["my custom message"]


def test_accept_nothing_to_commit(approved_workitem, monkeypatch):
    wi, paths = approved_workitem
    monkeypatch.chdir(paths.root.parent)

    def fake_run(args, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stderr = ""
        # diff --cached --quiet returns 0 → nothing staged
        return r

    with patch("conductor.cli.subprocess.run", side_effect=fake_run):
        result = runner.invoke(app, ["accept"])

    assert result.exit_code == 0
    assert "Nothing to commit" in result.output


def test_accept_push_flag(approved_workitem, monkeypatch):
    wi, paths = approved_workitem
    monkeypatch.chdir(paths.root.parent)

    called = []

    def fake_run(args, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stderr = ""
        if args[:2] == ["git", "diff"]:
            r.returncode = 1
        called.append(args[1])
        return r

    with patch("conductor.cli.subprocess.run", side_effect=fake_run):
        result = runner.invoke(app, ["accept", "--push"])

    assert result.exit_code == 0
    assert "push" in called
    assert "Pushed" in result.output


def test_accept_git_add_failure(approved_workitem, monkeypatch):
    wi, paths = approved_workitem
    monkeypatch.chdir(paths.root.parent)

    def fake_run(args, **kwargs):
        r = MagicMock()
        r.returncode = 1 if args[1] == "add" else 0
        r.stderr = "fatal: not a git repository"
        return r

    with patch("conductor.cli.subprocess.run", side_effect=fake_run):
        result = runner.invoke(app, ["accept"])

    assert result.exit_code == 1
    assert "git add failed" in result.output


def test_accept_no_active_workitem(tmp_path, monkeypatch):
    root = tmp_path / ".ai"
    scaffold_ai(root)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["accept"])
    assert result.exit_code == 1
    assert "No active workitem" in result.output
