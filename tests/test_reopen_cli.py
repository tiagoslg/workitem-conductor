"""CLI-level tests for `conductor reopen` — specifically, that it triggers
the summarizer (best-effort) without ever blocking the reopen itself.

No CLI-level reopen tests existed before M5 — `reopen_workitem()` (the
manager function) was tested directly, but the CLI command itself, which now
also resolves a provider and calls the summarizer, was not.
"""

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conductor.cli import app
from conductor.paths import AiPaths
from conductor.scaffold import scaffold_ai
from conductor.workitems.manager import approve_goal, create_workitem, load_memory
from conductor.core.worktree import worktree_path

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
    _git_init(root.parent)
    return AiPaths(root=root)


def test_reopen_triggers_summarizer_when_bound(paths: AiPaths, monkeypatch):
    monkeypatch.chdir(paths.root.parent)
    wi = create_workitem(paths, "bound summarizer")
    approve_goal(paths, wi.workitem_id)

    summary_echo = (
        "echo 'SUMMARY:' && echo '```yaml' && "
        "echo 'current_summary: reopened for a good reason' && echo '```'"
    )
    paths.repo_config.write_text(
        "name: test\n"
        "providers:\n"
        f"  echo_summarizer: {{ type: cli_one_shot, command: sh, args: [\"-c\", \"{summary_echo}\"], prompt_via: stdin }}\n"
        "roles:\n"
        "  summarizer: { provider: echo_summarizer }\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["reopen", "found a bug in review"])
    assert result.exit_code == 0
    assert "summarizer skipped" not in result.output

    memory = load_memory(paths, wi.workitem_id)
    assert memory.current_summary == "reopened for a good reason"


def test_reopen_succeeds_when_summarizer_unbound(paths: AiPaths, monkeypatch):
    monkeypatch.chdir(paths.root.parent)
    create_workitem(paths, "unbound summarizer")

    result = runner.invoke(app, ["reopen", "just a reason"])
    assert result.exit_code == 0
    assert "Reopened" in result.output
    assert "summarizer skipped" not in result.output  # unbound = silent no-op, not an error


def test_reopen_survives_broken_summarizer_binding(paths: AiPaths, monkeypatch):
    monkeypatch.chdir(paths.root.parent)
    create_workitem(paths, "broken summarizer")

    paths.repo_config.write_text(
        "name: test\n"
        "roles:\n"
        "  summarizer: { provider: does_not_exist }\n",  # unknown provider name
        encoding="utf-8",
    )

    result = runner.invoke(app, ["reopen", "reason"])
    assert result.exit_code == 0
    assert "Reopened" in result.output
    assert "summarizer skipped" in result.output


def test_reopen_from_later_step_summarizes_preserved_worktree(paths: AiPaths, monkeypatch):
    """`--from <role>` keeps the worktree (see cli.py::reopen), so the
    summarizer's diff must be computed against it, not the bare repo
    checkout — otherwise the in-progress work it's meant to compact is
    invisible to it.
    """
    monkeypatch.chdir(paths.root.parent)
    wi = create_workitem(paths, "from-step summarizer diff")
    approve_goal(paths, wi.workitem_id)

    # Detect whether stdin (the summarizer prompt) contains the marker file
    # left in the preserved worktree's uncommitted diff (`working_tree_diff`
    # is a `--stat` summary, so it shows the filename, not file content).
    detect_script = paths.root.parent / "detect_marker.py"
    detect_script.write_text(
        "import sys\n"
        "text = sys.stdin.read()\n"
        "found = 'worktree_marker.txt' in text\n"
        "print('SUMMARY:')\n"
        "print('```yaml')\n"
        "print('current_summary: ' + ('diff seen' if found else 'no diff'))\n"
        "print('```')\n",
        encoding="utf-8",
    )
    paths.repo_config.write_text(
        "name: test\n"
        "providers:\n"
        f"  detect_summarizer: {{ type: cli_one_shot, command: python3, args: [\"{detect_script}\"], prompt_via: stdin }}\n"
        "roles:\n"
        "  summarizer: { provider: detect_summarizer }\n",
        encoding="utf-8",
    )

    assert runner.invoke(app, ["execute", "--dry-run"]).exit_code == 0

    wt_path = worktree_path(paths, wi.workitem_id)
    assert wt_path.is_dir()
    (wt_path / "worktree_marker.txt").write_text("uncommitted work\n", encoding="utf-8")

    result = runner.invoke(app, ["reopen", "restart from implementer", "--from", "implementer"])
    assert result.exit_code == 0
    assert "worktree preserved" in result.output
    assert "summarizer skipped" not in result.output

    memory = load_memory(paths, wi.workitem_id)
    assert memory.current_summary == "diff seen"
