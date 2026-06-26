"""Tests for WorkspacePaths and the cross-project context builder."""

from __future__ import annotations

from pathlib import Path

import pytest

from conductor.paths import WorkspacePaths
from conductor.core.context import build_cross_project_section
from conductor.scaffold import scaffold_workspace
from conductor.workitems.manager import (
    create_workitem,
    get_active_id,
    list_workitems,
    load_workitem,
    approve_goal,
)


@pytest.fixture
def ws_paths(tmp_path: Path) -> WorkspacePaths:
    root = tmp_path / "workspaces" / "test-ws"
    scaffold_workspace(root, "test-ws")
    return WorkspacePaths(root=root, name="test-ws", project_roots=[])


@pytest.fixture
def two_repo_ws(tmp_path: Path) -> WorkspacePaths:
    fe = tmp_path / "fe-project"
    be = tmp_path / "be-project"
    fe.mkdir()
    be.mkdir()
    root = tmp_path / "workspaces" / "habit"
    scaffold_workspace(root, "habit")
    return WorkspacePaths(root=root, name="habit", project_roots=[fe, be])


# --- WorkspacePaths properties ---


def test_workspace_paths_has_expected_layout(ws_paths: WorkspacePaths):
    assert ws_paths.config.name == "config.yml"
    assert ws_paths.instructions.name == "instructions.md"
    assert ws_paths.roles_dir.name == "roles"
    assert ws_paths.workitems_dir.name == "workitems"
    assert ws_paths.active_pointer.name == "active_workitem.txt"


def test_workspace_paths_cwd_common_ancestor(tmp_path: Path):
    fe = tmp_path / "projects" / "fe"
    be = tmp_path / "projects" / "be"
    ws = WorkspacePaths(root=tmp_path / "ws", name="x", project_roots=[fe, be])
    assert ws.cwd == tmp_path / "projects"


def test_workspace_paths_cwd_no_projects(tmp_path: Path):
    ws = WorkspacePaths(root=tmp_path / "ws", name="x", project_roots=[])
    assert ws.cwd == Path.home()


# --- scaffold_workspace ---


def test_scaffold_workspace_creates_files(tmp_path: Path):
    root = tmp_path / "ws"
    result = scaffold_workspace(root, "my-ws")
    assert set(result.created) == {
        "config.yml", "instructions.md",
        "flows/workspace-change.yml", "roles/planner.md",
    }
    assert result.skipped == []
    assert (root / "config.yml").is_file()
    assert "my-ws" in (root / "instructions.md").read_text()
    assert (root / "flows" / "workspace-change.yml").is_file()
    assert (root / "roles" / "planner.md").is_file()


def test_scaffold_workspace_is_idempotent(tmp_path: Path):
    root = tmp_path / "ws"
    scaffold_workspace(root, "ws")
    result2 = scaffold_workspace(root, "ws")
    assert result2.created == []
    assert set(result2.skipped) == {
        "config.yml", "instructions.md",
        "flows/workspace-change.yml", "roles/planner.md",
    }


# --- workitem lifecycle via WorkspacePaths ---


def test_workspace_workitem_create_list_load(ws_paths: WorkspacePaths):
    wi = create_workitem(ws_paths, "FE bug caused by missing BE endpoint", flow="workspace-analysis")
    assert wi.workitem_id.startswith("20")
    assert wi.state.flow == "workspace-analysis"
    assert wi.goal.goal == "FE bug caused by missing BE endpoint"

    ids = list_workitems(ws_paths)
    assert wi.workitem_id in ids

    reloaded = load_workitem(ws_paths, wi.workitem_id)
    assert reloaded.goal.goal == wi.goal.goal


def test_workspace_active_pointer(ws_paths: WorkspacePaths):
    wi = create_workitem(ws_paths, "cross-project debug")
    assert get_active_id(ws_paths) == wi.workitem_id


def test_workspace_approve(ws_paths: WorkspacePaths):
    wi = create_workitem(ws_paths, "diagnose missing endpoint")
    updated = approve_goal(ws_paths, wi.workitem_id)
    assert updated.goal.approved is True
    assert updated.state.status == "ready"


# --- build_cross_project_section ---


def test_cross_project_section_includes_projects(two_repo_ws: WorkspacePaths):
    section = build_cross_project_section(two_repo_ws)
    assert "fe-project" in section
    assert "be-project" in section


def test_cross_project_section_includes_workspace_instructions(two_repo_ws: WorkspacePaths):
    two_repo_ws.instructions.write_text("FE calls BE at /api/v1/claims", encoding="utf-8")
    section = build_cross_project_section(two_repo_ws)
    assert "FE calls BE at /api/v1/claims" in section


def test_cross_project_section_includes_repo_instructions(two_repo_ws: WorkspacePaths, tmp_path: Path):
    fe = two_repo_ws.project_roots[0]
    ai = fe / ".ai"
    ai.mkdir()
    (ai / "instructions.md").write_text("FE uses React 18", encoding="utf-8")
    section = build_cross_project_section(two_repo_ws)
    assert "FE uses React 18" in section


def test_cross_project_section_notes_missing_ai(two_repo_ws: WorkspacePaths):
    section = build_cross_project_section(two_repo_ws)
    assert "conductor init" in section
