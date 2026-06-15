from datetime import date
from pathlib import Path

import pytest

from conductor.paths import AiPaths
from conductor.workitems.manager import (
    approve_goal,
    create_workitem,
    generate_id,
    get_active_id,
    list_workitems,
    load_workitem,
    reopen_workitem,
    save_state,
    slugify,
)
from conductor.workitems.models import GoalContract, WorkitemState


@pytest.fixture
def paths(tmp_path: Path) -> AiPaths:
    root = tmp_path / ".ai"
    root.mkdir()
    return AiPaths(root=root)


def test_slugify():
    assert slugify("Fix the policy discovery bug") == "fix-the-policy-discovery-bug"
    assert slugify("  Hello,  World!! ") == "hello-world"
    assert slugify("") == "workitem"


def test_generate_id_collision(tmp_path: Path):
    workitems = tmp_path / "workitems"
    workitems.mkdir()
    today = date(2026, 6, 13)

    first = generate_id("do a thing", workitems, today)
    assert first == "2026-06-13_do-a-thing"

    (workitems / first).mkdir()
    second = generate_id("do a thing", workitems, today)
    assert second == "2026-06-13_do-a-thing-2"


def test_create_workitem_writes_artifacts(paths: AiPaths):
    wi = create_workitem(paths, "fix the policy discovery bug")

    assert wi.directory.is_dir()
    assert (wi.directory / "goal.yml").is_file()
    assert (wi.directory / "state.yml").is_file()
    assert (wi.directory / "outputs").is_dir()
    assert (wi.directory / "reviews").is_dir()

    # round-trip the goal contract
    goal = GoalContract.from_yaml((wi.directory / "goal.yml").read_text())
    assert goal.goal == "fix the policy discovery bug"
    assert goal.approved is False

    # state defaults
    state = WorkitemState.from_yaml((wi.directory / "state.yml").read_text())
    assert state.stage == "defined"
    assert state.status == "draft"
    assert state.next_action == "approve_goal"
    assert state.artifacts["goal"] == "goal.yml"
    assert len(state.history) == 1


def test_active_pointer_round_trip(paths: AiPaths):
    wi = create_workitem(paths, "first goal")
    assert get_active_id(paths) == wi.workitem_id


def test_get_active_id_ignores_missing_dir(paths: AiPaths):
    paths.active_pointer.write_text("nonexistent-id\n", encoding="utf-8")
    assert get_active_id(paths) is None


def test_list_workitems(paths: AiPaths):
    a = create_workitem(paths, "alpha")
    b = create_workitem(paths, "beta")
    assert set(list_workitems(paths)) == {a.workitem_id, b.workitem_id}


def test_approve_goal_syncs_goal_and_state(paths: AiPaths):
    wi = create_workitem(paths, "approve me")
    assert wi.goal.approved is False

    updated = approve_goal(paths, wi.workitem_id)

    assert updated.goal.approved is True
    assert updated.state.status == "ready"
    assert updated.state.next_action == "execute"
    assert updated.state.history[-1].summary == "goal approved; ready to execute"

    # persisted to disk
    reloaded = load_workitem(paths, wi.workitem_id)
    assert reloaded.goal.approved is True
    assert reloaded.state.status == "ready"


def test_approve_goal_reconciles_manual_edit(paths: AiPaths):
    # Simulate a user who hand-edited goal.yml to approved: true but whose
    # state.yml still lags in draft.
    wi = create_workitem(paths, "manual approval")
    wi.goal.approved = True
    from conductor.workitems.manager import save_goal

    save_goal(paths, wi.workitem_id, wi.goal)

    updated = approve_goal(paths, wi.workitem_id)
    assert updated.state.status == "ready"
    assert updated.state.next_action == "execute"


def test_reopen_resets_state(paths: AiPaths):
    wi = create_workitem(paths, "fix the auth bug")
    approve_goal(paths, wi.workitem_id)
    # simulate a completed run
    state = load_workitem(paths, wi.workitem_id).state
    state.step_index = 3
    state.stage = "completed"
    state.status = "completed"
    state.fix_iterations = 2
    save_state(paths, state)

    updated = reopen_workitem(paths, wi.workitem_id, "still broken after review")

    assert updated.state.step_index == 0
    assert updated.state.stage == "defined"
    assert updated.state.status == "ready"
    assert updated.state.next_action == "execute"
    assert updated.state.fix_iterations == 0
    assert "reopened" in updated.state.history[-1].summary


def test_reopen_writes_reopen_md(paths: AiPaths):
    wi = create_workitem(paths, "add pagination")
    reopen_workitem(paths, wi.workitem_id, "pagination skipped edge case on empty results")
    reopen_file = paths.workitem_dir(wi.workitem_id) / "reopen.md"
    assert reopen_file.is_file()
    assert "pagination skipped edge case" in reopen_file.read_text(encoding="utf-8")


def test_reopen_with_step_index(paths: AiPaths):
    wi = create_workitem(paths, "improve search")
    updated = reopen_workitem(paths, wi.workitem_id, "review found a bug", step_index=2)
    assert updated.state.step_index == 2


def test_reopen_overwrites_previous_reopen_md(paths: AiPaths):
    wi = create_workitem(paths, "refactor endpoints")
    reopen_workitem(paths, wi.workitem_id, "first reason")
    reopen_workitem(paths, wi.workitem_id, "second reason")
    reopen_file = paths.workitem_dir(wi.workitem_id) / "reopen.md"
    assert reopen_file.read_text(encoding="utf-8") == "second reason"


def test_save_and_load_state(paths: AiPaths):
    wi = create_workitem(paths, "stateful goal")
    wi.state.status = "ready"
    wi.state.record("goal approved by human")
    save_state(paths, wi.state)

    reloaded = load_workitem(paths, wi.workitem_id)
    assert reloaded.state.status == "ready"
    assert reloaded.state.history[-1].summary == "goal approved by human"
