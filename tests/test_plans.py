"""Tests for the plans registry — scan, lint, mark, and the CLI surface."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from conductor.cli import app
from conductor.plans.frontmatter import parse_frontmatter, parse_frontmatter_and_body
from conductor.plans.lint import lint_plans
from conductor.plans.models import Plan, PlanFrontmatter
from conductor.plans.scan import scan_repo, scan_repos
from conductor.plans.write import mark_done, mark_ready
from conductor.workspaces import add_project, load_registry, save_registry

runner = CliRunner()


def _write_plan(repo: Path, name: str, frontmatter: str, body: str = "# Plan\n\nSome prose.\n") -> Path:
    plans_dir = repo / ".ai" / "execution_plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    path = plans_dir / name
    path.write_text(f"---\n{frontmatter}---\n\n{body}", encoding="utf-8")
    return path


BASE_FM = (
    "schema_version: 1\n"
    "id: {id}\n"
    "sprint: {sprint}\n"
    "primary_repo: repo-a\n"
    "status: {status}\n"
    "executable: true\n"
    "commits: {commits}\n"
    "depends_on: {depends_on}\n"
    "related: []\n"
    "blocked_until: null\n"
    "owner_team: null\n"
    "external_handoff: false\n"
    "created_at: 2026-07-17\n"
    "completed_at: null\n"
)


def _fm(id: str, status: str = "draft", sprint: str = "null", commits: str = "[]", depends_on: str = "[]") -> str:
    return BASE_FM.format(id=id, sprint=sprint, status=status, commits=commits, depends_on=depends_on)


# ---------------------------------------------------------------------------
# frontmatter parsing
# ---------------------------------------------------------------------------


def test_parse_frontmatter_returns_none_without_block():
    assert parse_frontmatter("# Just a heading\n\nNo frontmatter here.\n") is None


def test_parse_frontmatter_and_body_splits_correctly(tmp_path: Path):
    text = f"---\n{_fm('x')}---\n\n# Title\n\nBody text.\n"
    result = parse_frontmatter_and_body(text)
    assert result is not None
    data, body = result
    assert data["id"] == "x"
    assert body.strip().startswith("# Title")


def test_planfrontmatter_coerces_bare_yaml_dates():
    fm = PlanFrontmatter.model_validate(
        {"id": "x", "primary_repo": "r", "created_at": None}
    )
    assert fm.created_at is None
    import datetime as dt

    fm2 = PlanFrontmatter.model_validate({"id": "x", "primary_repo": "r", "created_at": dt.date(2026, 7, 17)})
    assert fm2.created_at == "2026-07-17"


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


def test_scan_repo_finds_plans(tmp_path: Path):
    repo = tmp_path / "repo-a"
    _write_plan(repo, "2026-07-17_one.md", _fm("one"))
    _write_plan(repo, "2026-07-17_two.md", _fm("two"))
    plans, warnings = scan_repo(repo, "repo-a")
    assert warnings == []
    assert {p.id for p in plans} == {"one", "two"}
    assert all(p.repo == "repo-a" for p in plans)


def test_scan_repo_no_execution_plans_dir_returns_empty(tmp_path: Path):
    plans, warnings = scan_repo(tmp_path / "empty", "empty")
    assert plans == []
    assert warnings == []


def test_scan_repo_surfaces_warning_for_bad_frontmatter(tmp_path: Path):
    repo = tmp_path / "repo-a"
    plans_dir = repo / ".ai" / "execution_plans"
    plans_dir.mkdir(parents=True)
    (plans_dir / "bad.md").write_text("# No frontmatter at all\n", encoding="utf-8")
    plans, warnings = scan_repo(repo, "repo-a")
    assert plans == []
    assert len(warnings) == 1
    assert "no valid frontmatter" in warnings[0].reason


def test_scan_repos_aggregates_across_repos(tmp_path: Path):
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    _write_plan(repo_a, "2026-07-17_a.md", _fm("a"))
    _write_plan(repo_b, "2026-07-17_b.md", _fm("b"))
    plans, warnings = scan_repos({"repo-a": repo_a, "repo-b": repo_b})
    assert warnings == []
    assert {p.id for p in plans} == {"a", "b"}


# ---------------------------------------------------------------------------
# lint
# ---------------------------------------------------------------------------


def _plan(id: str, **overrides) -> Plan:
    data = {
        "schema_version": 1,
        "id": id,
        "primary_repo": "repo-a",
        "status": "draft",
        "executable": True,
        "commits": [],
        "depends_on": [],
        "related": [],
        "external_handoff": False,
    }
    data.update(overrides)
    return Plan(frontmatter=PlanFrontmatter.model_validate(data), repo="repo-a", path=Path(f"/tmp/{id}.md"))


def test_lint_flags_unknown_dependency():
    plans = [_plan("a", depends_on=["ghost"])]
    issues = lint_plans(plans)
    assert any("unknown id 'ghost'" in i.message for i in issues)


def test_lint_does_not_require_commits_for_non_executable_done_plan():
    plans = [_plan("overview", status="done", executable=False, commits=[])]
    issues = lint_plans(plans)
    assert issues == []


def test_lint_flags_cycle():
    plans = [_plan("a", depends_on=["b"]), _plan("b", depends_on=["a"])]
    issues = lint_plans(plans)
    assert any("cycle" in i.message for i in issues)


def test_lint_flags_done_without_commits():
    plans = [_plan("a", status="done", commits=[])]
    issues = lint_plans(plans)
    assert any("commits is empty" in i.message for i in issues)


def test_lint_passes_done_with_commits():
    plans = [_plan("a", status="done", commits=["abc123"])]
    issues = lint_plans(plans)
    assert issues == []


def test_lint_flags_done_depending_on_not_done():
    plans = [
        _plan("a", status="done", commits=["x"], depends_on=["b"]),
        _plan("b", status="draft"),
    ]
    issues = lint_plans(plans)
    assert any("not done" in i.message for i in issues)


def test_lint_external_handoff_requires_inline_summary(tmp_path: Path):
    repo = tmp_path / "repo-a"
    dep_fm = _fm("dep")
    _write_plan(repo, "2026-07-17_dep.md", dep_fm)
    main_fm = _fm("main", depends_on="[dep]").replace("external_handoff: false", "external_handoff: true")
    _write_plan(repo, "2026-07-17_main.md", main_fm, body="# Main\n\nThis body never names its prerequisite plan.\n")
    plans, _ = scan_repo(repo, "repo-a")
    issues = lint_plans(plans)
    assert any("no inline summary" in i.message for i in issues)


def test_lint_no_issues_for_clean_set():
    plans = [_plan("a"), _plan("b", depends_on=["a"])]
    assert lint_plans(plans) == []


# ---------------------------------------------------------------------------
# write (mark ready / done)
# ---------------------------------------------------------------------------


def test_mark_ready_transitions_status(tmp_path: Path):
    repo = tmp_path / "repo-a"
    path = _write_plan(repo, "2026-07-17_a.md", _fm("a", status="draft"))
    mark_ready(path)
    plans, _ = scan_repo(repo, "repo-a")
    assert plans[0].frontmatter.status == "ready"


def test_mark_done_appends_commit_and_stamps_completed_at(tmp_path: Path):
    repo = tmp_path / "repo-a"
    path = _write_plan(repo, "2026-07-17_a.md", _fm("a", status="ready"))
    mark_done(path, commit="abc123")
    plans, _ = scan_repo(repo, "repo-a")
    fm = plans[0].frontmatter
    assert fm.status == "done"
    assert fm.commits == ["abc123"]
    assert fm.completed_at is not None


def test_mark_done_preserves_body(tmp_path: Path):
    repo = tmp_path / "repo-a"
    path = _write_plan(repo, "2026-07-17_a.md", _fm("a"), body="# Title\n\nImportant prose that must survive.\n")
    mark_done(path)
    assert "Important prose that must survive." in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@pytest.fixture
def registered_repo(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo-a"
    repo.mkdir()
    monkeypatch.chdir(repo)
    registry = load_registry()
    add_project(registry, repo)
    save_registry(registry)
    return repo


def test_cli_plans_list_shows_scanned_plans(registered_repo: Path):
    _write_plan(registered_repo, "2026-07-17_a.md", _fm("a"))
    result = runner.invoke(app, ["plans", "list"])
    assert result.exit_code == 0
    assert "a" in result.output


def test_cli_plans_list_empty_says_so(registered_repo: Path):
    result = runner.invoke(app, ["plans", "list"])
    assert result.exit_code == 0
    assert "No plans found" in result.output


def test_cli_plans_lint_exits_nonzero_on_issues(registered_repo: Path):
    _write_plan(registered_repo, "2026-07-17_a.md", _fm("a", depends_on="[ghost]"))
    result = runner.invoke(app, ["plans", "lint"])
    assert result.exit_code == 1
    assert "ghost" in result.output


def test_cli_plans_lint_clean_exits_zero(registered_repo: Path):
    _write_plan(registered_repo, "2026-07-17_a.md", _fm("a"))
    result = runner.invoke(app, ["plans", "lint"])
    assert result.exit_code == 0
    assert "No issues" in result.output


def test_cli_plans_mark_ready_then_done(registered_repo: Path):
    _write_plan(registered_repo, "2026-07-17_a.md", _fm("a"))
    result = runner.invoke(app, ["plans", "mark", "a", "ready"])
    assert result.exit_code == 0
    result = runner.invoke(app, ["plans", "mark", "a", "done", "--commit", "abc123"])
    assert result.exit_code == 0
    result = runner.invoke(app, ["plans", "list"])
    assert "done" in result.output


def test_cli_plans_mark_rejects_unknown_status(registered_repo: Path):
    _write_plan(registered_repo, "2026-07-17_a.md", _fm("a"))
    result = runner.invoke(app, ["plans", "mark", "a", "bogus"])
    assert result.exit_code == 1


def test_cli_plans_mark_rejects_unknown_id(registered_repo: Path):
    result = runner.invoke(app, ["plans", "mark", "does-not-exist", "ready"])
    assert result.exit_code == 1


def test_cli_plans_ready_lists_only_ready_with_satisfied_deps(registered_repo: Path):
    _write_plan(registered_repo, "2026-07-17_a.md", _fm("a", status="ready"))
    _write_plan(registered_repo, "2026-07-17_b.md", _fm("b", status="ready", depends_on="[a]"))
    result = runner.invoke(app, ["plans", "ready"])
    assert result.exit_code == 0
    assert "a" in result.output
    assert "b" not in result.output


def test_cli_plans_table_renders_markdown(registered_repo: Path):
    _write_plan(registered_repo, "2026-07-17_a.md", _fm("a"))
    result = runner.invoke(app, ["plans", "table"])
    assert result.exit_code == 0
    assert "| id | repo | status | depends_on | commits |" in result.output
    assert "| a |" in result.output


def test_cli_workspace_list_shows_plan_count(registered_repo: Path):
    _write_plan(registered_repo, "2026-07-17_a.md", _fm("a"))
    _write_plan(registered_repo, "2026-07-17_b.md", _fm("b"))
    result = runner.invoke(app, ["workspace", "list"])
    assert result.exit_code == 0
    assert "2" in result.output
