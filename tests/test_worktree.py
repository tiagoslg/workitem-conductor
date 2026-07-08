"""Tests for the git worktree isolation module."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from conductor.core.worktree import (
    branch_name,
    commit_worktree,
    create_worktree,
    diff_stat,
    merge_worktree,
    remove_worktree,
    working_tree_diff,
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
    assert worktree_path(paths, "wi-001") == paths.worktree_dir("wi-001")
    # must live under the central data dir, not inside .ai/
    assert not str(worktree_path(paths, "wi-001")).startswith(str(paths.root))


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


# ---------------------------------------------------------------------------
# diff_stat / working_tree_diff — real git repo, no mocking (the scratch-index
# behavior can't be verified against a fake subprocess.run).
# ---------------------------------------------------------------------------

def _git_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "tracked.txt").write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
    return root


def test_diff_stat_not_a_repo_returns_none(tmp_path: Path):
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    assert diff_stat(not_a_repo) is None


def test_diff_stat_counts_untracked_and_modified(tmp_path: Path):
    repo = _git_repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("original\nmore\n", encoding="utf-8")
    (repo / "new-file.txt").write_text("brand new\n", encoding="utf-8")

    stats = diff_stat(repo)
    assert stats == {"files_changed": 2, "insertions": 2, "deletions": 0}


def test_diff_stat_does_not_touch_real_index(tmp_path: Path):
    """The scratch-index technique must leave the repo's real staging area untouched."""
    repo = _git_repo(tmp_path / "repo")
    (repo / "new-file.txt").write_text("brand new\n", encoding="utf-8")

    diff_stat(repo)

    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True,
    )
    # "??" means untracked/unstaged — if diff_stat had staged it for real,
    # this would read "A " instead.
    assert status.stdout.strip() == "?? new-file.txt"


def test_working_tree_diff_shows_untracked_files(tmp_path: Path):
    """Plain `git diff` never shows untracked files; this must, via the scratch index."""
    repo = _git_repo(tmp_path / "repo")
    (repo / "new-file.txt").write_text("brand new\n", encoding="utf-8")

    diff_text = working_tree_diff(repo)
    assert diff_text is not None
    assert "new-file.txt" in diff_text

    # and still no side effect on the real index
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True,
    )
    assert status.stdout.strip() == "?? new-file.txt"
