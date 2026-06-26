"""Tests for the git worktree isolation module."""

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from conductor.core.worktree import (
    branch_name,
    commit_worktree,
    create_worktree,
    merge_worktree,
    remove_worktree,
    worktree_path,
)
from conductor.paths import AiPaths
from conductor.scaffold import scaffold_ai


@pytest.fixture
def paths(tmp_path: Path) -> AiPaths:
    root = tmp_path / ".ai"
    scaffold_ai(root)
    return AiPaths(root=root)


# ---------------------------------------------------------------------------
# branch_name / worktree_path
# ---------------------------------------------------------------------------

def test_branch_name():
    assert branch_name("wi-001") == "conductor/wi-001"


def test_worktree_path(paths: AiPaths):
    assert worktree_path(paths, "wi-001") == paths.root / "worktrees" / "wi-001"


# ---------------------------------------------------------------------------
# create_worktree
# ---------------------------------------------------------------------------

def _git_ok(returncode=0, stdout="", stderr=""):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def test_create_worktree_new_branch(paths: AiPaths):
    """Creates a new branch worktree when branch does not exist yet."""
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        # show-ref --verify → branch does not exist
        if "show-ref" in cmd:
            return _git_ok(returncode=1)
        return _git_ok()

    with patch("conductor.core.worktree.subprocess.run", side_effect=fake_run):
        result = create_worktree(paths, "wi-001")

    assert result == worktree_path(paths, "wi-001")
    # last git command must be the -b variant
    new_branch_call = next(c for c in calls if "worktree" in c and "add" in c)
    assert "-b" in new_branch_call
    assert "conductor/wi-001" in new_branch_call


def test_create_worktree_existing_branch(paths: AiPaths):
    """Adds a worktree against an existing branch (no -b flag)."""
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if "show-ref" in cmd:
            return _git_ok(returncode=0)  # branch exists
        return _git_ok()

    with patch("conductor.core.worktree.subprocess.run", side_effect=fake_run):
        result = create_worktree(paths, "wi-001")

    new_branch_call = next(c for c in calls if "worktree" in c and "add" in c)
    assert "-b" not in new_branch_call


def test_create_worktree_reuses_valid_existing(paths: AiPaths, tmp_path: Path):
    """Returns existing path if the worktree is already registered in git."""
    wt_path = worktree_path(paths, "wi-001")
    wt_path.mkdir(parents=True)

    def fake_run(cmd, **kw):
        if "list" in cmd:
            return _git_ok(stdout=f"worktree {wt_path}\n")
        return _git_ok()

    with patch("conductor.core.worktree.subprocess.run", side_effect=fake_run):
        result = create_worktree(paths, "wi-001")

    assert result == wt_path


def test_create_worktree_raises_on_git_failure(paths: AiPaths):
    def fake_run(cmd, **kw):
        if "show-ref" in cmd:
            return _git_ok(returncode=1)
        if "worktree" in cmd and "add" in cmd:
            return _git_ok(returncode=128, stderr="fatal: not a git repo")
        return _git_ok()

    with patch("conductor.core.worktree.subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="git worktree add failed"):
            create_worktree(paths, "wi-001")


# ---------------------------------------------------------------------------
# commit_worktree
# ---------------------------------------------------------------------------

def test_commit_worktree_returns_true_when_dirty(paths: AiPaths):
    wt_path = worktree_path(paths, "wi-001")
    wt_path.mkdir(parents=True)

    def fake_run(cmd, **kw):
        r = _git_ok()
        if cmd[:2] == ["git", "diff"]:
            r.returncode = 1  # dirty
        return r

    with patch("conductor.core.worktree.subprocess.run", side_effect=fake_run):
        assert commit_worktree(paths, "wi-001", "my message") is True


def test_commit_worktree_returns_false_when_clean(paths: AiPaths):
    wt_path = worktree_path(paths, "wi-001")
    wt_path.mkdir(parents=True)

    def fake_run(cmd, **kw):
        return _git_ok()  # diff returns 0 → clean

    with patch("conductor.core.worktree.subprocess.run", side_effect=fake_run):
        assert commit_worktree(paths, "wi-001", "my message") is False


def test_commit_worktree_raises_on_add_failure(paths: AiPaths):
    wt_path = worktree_path(paths, "wi-001")
    wt_path.mkdir(parents=True)

    def fake_run(cmd, **kw):
        if cmd[1] == "add":
            return _git_ok(returncode=1, stderr="error")
        return _git_ok()

    with patch("conductor.core.worktree.subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="git add failed"):
            commit_worktree(paths, "wi-001", "msg")


# ---------------------------------------------------------------------------
# merge_worktree
# ---------------------------------------------------------------------------

def test_merge_worktree_passes_no_ff(paths: AiPaths):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return _git_ok()

    with patch("conductor.core.worktree.subprocess.run", side_effect=fake_run):
        merge_worktree(paths, "wi-001", "merge msg")

    merge_call = calls[0]
    assert "--no-ff" in merge_call
    assert "conductor/wi-001" in merge_call


def test_merge_worktree_raises_on_conflict(paths: AiPaths):
    def fake_run(cmd, **kw):
        return _git_ok(returncode=1, stderr="CONFLICT")

    with patch("conductor.core.worktree.subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="git merge failed"):
            merge_worktree(paths, "wi-001", "msg")


# ---------------------------------------------------------------------------
# remove_worktree
# ---------------------------------------------------------------------------

def test_remove_worktree_safe_when_absent(paths: AiPaths):
    """remove_worktree is a no-op if the directory does not exist."""
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return _git_ok()

    with patch("conductor.core.worktree.subprocess.run", side_effect=fake_run):
        remove_worktree(paths, "wi-001")

    # only prune should run — no worktree remove
    assert not any("remove" in c for c in calls)


def test_remove_worktree_with_delete_branch(paths: AiPaths):
    wt_path = worktree_path(paths, "wi-001")
    wt_path.mkdir(parents=True)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if "show-ref" in cmd:
            return _git_ok(returncode=0)  # branch exists
        return _git_ok()

    with patch("conductor.core.worktree.subprocess.run", side_effect=fake_run):
        remove_worktree(paths, "wi-001", delete_branch=True)

    branch_delete = next((c for c in calls if "branch" in c and "-D" in c), None)
    assert branch_delete is not None
    assert "conductor/wi-001" in branch_delete
