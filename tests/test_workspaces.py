from pathlib import Path

import pytest

from conductor.workspaces import (
    DEFAULT_WORKSPACE,
    WorkspaceRegistryError,
    add_project,
    list_projects,
    load_registry,
    registry_path,
    remove_project,
    save_registry,
)


@pytest.fixture(autouse=True)
def config_home(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CONDUCTOR_CONFIG_HOME", str(tmp_path / "cfg"))
    return tmp_path


def test_registry_path_uses_override(config_home: Path):
    assert registry_path() == config_home / "cfg" / "conductor" / "workspaces.yml"


def test_defaults_when_absent():
    reg = load_registry()
    assert DEFAULT_WORKSPACE in reg.workspaces
    assert list_projects(reg) == []


def test_add_list_remove_round_trip(tmp_path: Path):
    project = tmp_path / "svc"
    (project / ".ai").mkdir(parents=True)

    reg = load_registry()
    resolved, added, has_ai = add_project(reg, project)
    save_registry(reg)

    assert added is True
    assert has_ai is True
    assert resolved == str(project.resolve())

    # persisted to disk and reloads
    reg2 = load_registry()
    assert list_projects(reg2) == [str(project.resolve())]

    # remove
    assert remove_project(reg2, project) is True
    save_registry(reg2)
    assert list_projects(load_registry()) == []


def test_add_is_idempotent_and_reports_missing_ai(tmp_path: Path):
    project = tmp_path / "no-ai"
    project.mkdir()

    reg = load_registry()
    _, added, has_ai = add_project(reg, project)
    assert added is True
    assert has_ai is False  # registered anyway, but flagged

    _, added_again, _ = add_project(reg, project)
    assert added_again is False  # deduped
    assert list_projects(reg) == [str(project.resolve())]


def test_named_workspace_isolation(tmp_path: Path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()

    reg = load_registry()
    add_project(reg, a, workspace="work")
    add_project(reg, b, workspace="personal")

    assert list_projects(reg, "work") == [str(a.resolve())]
    assert list_projects(reg, "personal") == [str(b.resolve())]
    assert set(list_projects(reg)) == {str(a.resolve()), str(b.resolve())}

    # remove scoped to one workspace only
    remove_project(reg, a, workspace="personal")
    assert list_projects(reg, "work") == [str(a.resolve())]


def test_invalid_registry_raises(config_home: Path):
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("workspaces: [not, a, mapping]\n", encoding="utf-8")
    with pytest.raises(WorkspaceRegistryError):
        load_registry()
