"""Git worktree isolation for workitem execution.

Each workitem gets its own worktree under the central data directory
(``AiPaths.worktree_dir``, outside the git-tracked repo) on branch
conductor/<id>. The implementer runs there, leaving the main working tree
untouched. The branch is the audit trail; the worktree directory is a
temporary working space, cleaned up on accept or reopen.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..paths import AiPaths

_SHORTSTAT_RE = re.compile(
    r"(\d+) files? changed"
    r"(?:, (\d+) insertions?\(\+\))?"
    r"(?:, (\d+) deletions?\(-\))?"
)


def branch_name(workitem_id: str) -> str:
    return f"conductor/{workitem_id}"


def _scratch_index_diff(cwd: Path, diff_args: list[str]) -> str | None:
    """Run ``git diff --cached <diff_args>`` against a **scratch index**.

    The scratch index (a temp file via ``GIT_INDEX_FILE``) is seeded from
    HEAD (``git read-tree``) and then populated with ``git add -A`` against
    the real working tree — so untracked files and deletions are picked up —
    without ever touching the repo's real ``.git/index``. Reading (or
    computing metrics from) the working tree must not have side effects on
    its staging area: an earlier version ran a real ``git add -A``, which left
    everything staged for real and made `conductor inspect`'s plain
    ``git diff --stat`` report no changes even when a run had produced some.

    Returns ``None`` on any failure (not a git repo, no commits yet, no git
    binary, ``cwd`` missing) — this is read-only diagnostics, never load-bearing.
    """
    if not cwd.is_dir():
        return None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
            read_tree = subprocess.run(
                ["git", "read-tree", "HEAD"], cwd=cwd, capture_output=True, text=True, env=env,
            )
            if read_tree.returncode != 0:
                return None
            add = subprocess.run(
                ["git", "add", "-A"], cwd=cwd, capture_output=True, text=True, env=env,
            )
            if add.returncode != 0:
                return None
            result = subprocess.run(
                ["git", "diff", "--cached", *diff_args],
                cwd=cwd, capture_output=True, text=True, env=env,
            )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def diff_stat(cwd: Path) -> dict | None:
    """Best-effort ``{files_changed, insertions, deletions}`` for ``cwd``, or ``None``."""
    stdout = _scratch_index_diff(cwd, ["--shortstat"])
    if stdout is None:
        return None
    m = _SHORTSTAT_RE.search(stdout)
    if not m:
        return {"files_changed": 0, "insertions": 0, "deletions": 0}
    return {
        "files_changed": int(m.group(1)),
        "insertions": int(m.group(2) or 0),
        "deletions": int(m.group(3) or 0),
    }


def working_tree_diff(cwd: Path) -> str | None:
    """Human-readable ``git diff --stat`` for ``cwd``, including untracked files.

    Plain ``git diff`` never shows untracked files regardless of ``HEAD``
    arguments — only staged content is comparable. This uses the same
    scratch-index technique as ``diff_stat`` so new files created by an
    implementer (the common case) show up without staging them for real.
    """
    return _scratch_index_diff(cwd, ["--stat"])


def worktree_path(paths: AiPaths, workitem_id: str) -> Path:
    return paths.worktree_dir(workitem_id)


def _is_registered(repo_root: Path, wt_path: Path) -> bool:
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo_root, capture_output=True, text=True,
    )
    return str(wt_path) in result.stdout


def _branch_exists(repo_root: Path, branch: str) -> bool:
    result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo_root, capture_output=True,
    )
    return result.returncode == 0


def create_worktree(
    paths: AiPaths, workitem_id: str, source_branch: str | None = None
) -> Path:
    """Create (or reuse) a worktree at the central worktree dir on branch conductor/<id>.

    If a valid worktree already exists (resumed execution), returns the path
    as-is. A stale directory without a matching git registration is removed and
    recreated. When ``source_branch`` is given and the conductor branch does not
    yet exist, the new branch is created from ``source_branch`` instead of the
    current HEAD. Raises RuntimeError if git fails.
    """
    wt_path = worktree_path(paths, workitem_id)
    branch = branch_name(workitem_id)
    repo_root = paths.cwd

    if wt_path.is_dir():
        if _is_registered(repo_root, wt_path):
            return wt_path
        shutil.rmtree(wt_path)

    wt_path.parent.mkdir(parents=True, exist_ok=True)

    if _branch_exists(repo_root, branch):
        cmd = ["git", "worktree", "add", str(wt_path), branch]
    else:
        cmd = ["git", "worktree", "add", "-b", branch, str(wt_path)]
        if source_branch:
            cmd.append(source_branch)

    result = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git worktree add failed: {result.stderr.strip()}")

    return wt_path


def commit_worktree(paths: AiPaths, workitem_id: str, message: str) -> bool:
    """Stage all changes in the worktree and commit them.

    Returns True if a commit was made, False if the worktree was clean.
    Raises RuntimeError on git failure.
    """
    wt_path = worktree_path(paths, workitem_id)

    stage = subprocess.run(
        ["git", "add", "-A"], cwd=wt_path, capture_output=True, text=True,
    )
    if stage.returncode != 0:
        raise RuntimeError(f"git add failed in worktree: {stage.stderr.strip()}")

    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=wt_path,
    )
    if diff.returncode == 0:
        return False

    commit = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=wt_path, capture_output=True, text=True,
    )
    if commit.returncode != 0:
        raise RuntimeError(f"git commit failed in worktree: {commit.stderr.strip()}")

    return True


def merge_worktree(
    paths: AiPaths, workitem_id: str, message: str, target_branch: str | None = None
) -> None:
    """Merge conductor/<id> into ``target_branch`` (or the current HEAD).

    Uses --no-ff so the merge commit always names the agent branch.
    When ``target_branch`` is given the repo is checked out to that branch
    before merging so the result always lands in the right place regardless
    of what was checked out in the caller's working tree.
    Raises RuntimeError on checkout or merge failure.
    """
    branch = branch_name(workitem_id)
    repo_root = paths.cwd

    if target_branch:
        checkout = subprocess.run(
            ["git", "checkout", target_branch],
            cwd=repo_root, capture_output=True, text=True,
        )
        if checkout.returncode != 0:
            raise RuntimeError(
                f"git checkout {target_branch!r} failed: {checkout.stderr.strip()}"
            )

    result = subprocess.run(
        ["git", "merge", "--no-ff", "-m", message, branch],
        cwd=repo_root, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git merge failed (conflicts?):\n"
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


def remove_worktree(
    paths: AiPaths, workitem_id: str, *, delete_branch: bool = False
) -> None:
    """Remove the worktree directory and prune stale references.

    Safe to call even if the worktree does not exist. With ``delete_branch=True``
    the conductor/<id> branch is also deleted (e.g. on reopen, where the work is
    being discarded rather than merged).
    """
    wt_path = worktree_path(paths, workitem_id)
    branch = branch_name(workitem_id)
    repo_root = paths.cwd

    if wt_path.is_dir():
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt_path)],
            cwd=repo_root, capture_output=True, text=True,
        )

    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=repo_root, capture_output=True, text=True,
    )

    if delete_branch and _branch_exists(repo_root, branch):
        subprocess.run(
            ["git", "branch", "-D", branch],
            cwd=repo_root, capture_output=True, text=True,
        )
