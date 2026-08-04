"""Tests for `conductor plans sync` — the opencode.db correlation layer.

Builds a throwaway sqlite file per test with the real `session`/`part`
columns the reader relies on, rather than mocking, matching the "test
against something structurally real" approach `test_plans.py` uses for the
filesystem side.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from conductor.cli import app
from conductor.plans.models import Plan, PlanFrontmatter
from conductor.plans.opencode_db import OpenCodeDbNotFound, find_marked_session_ids, session_snapshots
from conductor.plans.sync import read_snapshot, sync_plan
from conductor.workspaces import add_project, load_registry, save_registry

runner = CliRunner()


def _make_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute(
        """CREATE TABLE session (
            id TEXT PRIMARY KEY, parent_id TEXT, agent TEXT, model TEXT,
            directory TEXT, cost REAL, tokens_input INTEGER, tokens_output INTEGER,
            tokens_reasoning INTEGER, tokens_cache_read INTEGER, tokens_cache_write INTEGER,
            time_created INTEGER, time_updated INTEGER
        )"""
    )
    con.execute(
        """CREATE TABLE part (
            id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,
            time_created INTEGER, time_updated INTEGER, data TEXT
        )"""
    )
    con.commit()
    con.close()


def _insert_session(db_path: Path, **kwargs) -> None:
    defaults = dict(
        id="ses_x", parent_id=None, agent=None, model=None, directory=None,
        cost=0.0, tokens_input=0, tokens_output=0, tokens_reasoning=0,
        tokens_cache_read=0, tokens_cache_write=0, time_created=0, time_updated=0,
    )
    defaults.update(kwargs)
    con = sqlite3.connect(db_path)
    con.execute(
        """INSERT INTO session VALUES (:id, :parent_id, :agent, :model, :directory, :cost,
           :tokens_input, :tokens_output, :tokens_reasoning, :tokens_cache_read,
           :tokens_cache_write, :time_created, :time_updated)""",
        defaults,
    )
    con.commit()
    con.close()


def _insert_part(db_path: Path, session_id: str, text: str, part_id: str | None = None) -> None:
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)",
        (part_id or f"prt_{session_id}_{text[:8]}", "msg_x", session_id, 0, 0, json.dumps({"type": "text", "text": text})),
    )
    con.commit()
    con.close()


def _plan(id: str, tmp_path: Path) -> Plan:
    fm = PlanFrontmatter.model_validate({"id": id, "primary_repo": "repo-a"})
    return Plan(frontmatter=fm, repo="repo-a", path=tmp_path / f"{id}.md")


# ---------------------------------------------------------------------------
# opencode_db
# ---------------------------------------------------------------------------


def test_find_marked_session_ids_exact_match_only(tmp_path: Path):
    db = tmp_path / "oc.db"
    _make_db(db)
    _insert_part(db, "ses_a", "PLAN_ID: plan-a\n\nsummary")
    _insert_part(db, "ses_b", "PLAN_ID: plan-a-extra\n\nsummary")
    ids = find_marked_session_ids(db, "plan-a")
    assert ids == {"ses_a"}


def test_find_marked_session_ids_no_match(tmp_path: Path):
    db = tmp_path / "oc.db"
    _make_db(db)
    _insert_part(db, "ses_a", "nothing relevant here")
    assert find_marked_session_ids(db, "plan-a") == set()


def test_session_snapshots_includes_children(tmp_path: Path):
    db = tmp_path / "oc.db"
    _make_db(db)
    _insert_session(
        db, id="ses_parent", agent="workitem-conductor",
        model=json.dumps({"id": "gpt-5.5", "providerID": "openai"}),
        tokens_input=100, tokens_output=10,
    )
    _insert_session(db, id="ses_child", parent_id="ses_parent", agent="implementer", tokens_input=50, tokens_output=5)
    _insert_session(db, id="ses_unrelated", agent="implementer", tokens_input=999, tokens_output=999)
    snaps = session_snapshots(db, {"ses_parent"})
    ids = {s.id for s in snaps}
    assert ids == {"ses_parent", "ses_child"}
    parent = next(s for s in snaps if s.id == "ses_parent")
    assert parent.model == "openai/gpt-5.5"


def test_opencode_db_not_found(tmp_path: Path):
    with pytest.raises(OpenCodeDbNotFound):
        find_marked_session_ids(tmp_path / "missing.db", "plan-a")


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------


def test_sync_plan_writes_snapshot_with_matches(tmp_path: Path):
    db = tmp_path / "oc.db"
    _make_db(db)
    _insert_session(db, id="ses_parent", agent="workitem-conductor", tokens_input=100, tokens_output=10)
    _insert_session(db, id="ses_child", parent_id="ses_parent", agent="implementer", tokens_input=50, tokens_output=5)
    _insert_part(db, "ses_parent", "PLAN_ID: my-plan\n\nsummary")

    plan = _plan("my-plan", tmp_path)
    snap = sync_plan(plan, db_path=db)
    assert snap.matched_via == "plan_id_marker"
    assert snap.totals.session_count == 2
    assert snap.totals.tokens_input == 150
    assert snap.totals.tokens_output == 15

    reread = read_snapshot("my-plan")
    assert reread is not None
    assert reread.totals.session_count == 2


def test_sync_plan_no_matches_is_not_an_error(tmp_path: Path):
    db = tmp_path / "oc.db"
    _make_db(db)
    plan = _plan("lonely-plan", tmp_path)
    snap = sync_plan(plan, db_path=db)
    assert snap.matched_via == "none"
    assert snap.sessions == []
    assert snap.totals.session_count == 0


def test_read_snapshot_missing_returns_none():
    assert read_snapshot("never-synced") is None


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


def _write_plan(repo: Path, name: str, plan_id: str) -> Path:
    plans_dir = repo / ".ai" / "execution_plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    path = plans_dir / name
    fm = (
        "schema_version: 1\n"
        f"id: {plan_id}\n"
        "sprint: null\n"
        "primary_repo: repo-a\n"
        "status: draft\n"
        "executable: true\n"
        "commits: []\n"
        "depends_on: []\n"
        "related: []\n"
        "blocked_until: null\n"
        "owner_team: null\n"
        "external_handoff: false\n"
        "created_at: 2026-07-17\n"
        "completed_at: null\n"
    )
    path.write_text(f"---\n{fm}---\n\n# Plan\n\nBody.\n", encoding="utf-8")
    return path


def test_cli_plans_sync_reports_matches(registered_repo: Path, tmp_path: Path):
    _write_plan(registered_repo, "2026-07-17_a.md", "a")
    db = tmp_path / "oc.db"
    _make_db(db)
    _insert_session(db, id="ses_a", agent="workitem-conductor", tokens_input=10, tokens_output=1)
    _insert_part(db, "ses_a", "PLAN_ID: a\n\nsummary")

    result = runner.invoke(app, ["plans", "sync", "--db", str(db)])
    assert result.exit_code == 0
    assert "1 sessions" in result.output


def test_cli_plans_sync_missing_db_exits_nonzero(registered_repo: Path, tmp_path: Path):
    _write_plan(registered_repo, "2026-07-17_a.md", "a")
    result = runner.invoke(app, ["plans", "sync", "--db", str(tmp_path / "missing.db")])
    assert result.exit_code == 1


def test_cli_plans_list_with_execution_shows_dash_before_sync(registered_repo: Path):
    _write_plan(registered_repo, "2026-07-17_a.md", "a")
    result = runner.invoke(app, ["plans", "list", "--with-execution"])
    assert result.exit_code == 0
    assert "sessions" in result.output
    assert "Run `conductor plans sync`" in result.output


def test_cli_plans_list_with_execution_shows_totals_after_sync(registered_repo: Path, tmp_path: Path):
    _write_plan(registered_repo, "2026-07-17_a.md", "a")
    db = tmp_path / "oc.db"
    _make_db(db)
    _insert_session(db, id="ses_a", agent="workitem-conductor", tokens_input=10, tokens_output=1)
    _insert_part(db, "ses_a", "PLAN_ID: a\n\nsummary")
    runner.invoke(app, ["plans", "sync", "--db", str(db)])

    result = runner.invoke(app, ["plans", "list", "--with-execution"])
    assert result.exit_code == 0
    assert "10+1" in result.output
