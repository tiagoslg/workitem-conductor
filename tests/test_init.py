from pathlib import Path

from conductor.scaffold import scaffold_ai

EXPECTED = {
    "repo.yml",
    "instructions.md",
    "flows/simple-change.yml",
    "roles/planner.md",
    "roles/implementer.md",
    "roles/reviewer.md",
    "roles/refiner.md",
    ".gitignore",
}


def test_scaffold_creates_expected_files(tmp_path: Path):
    root = tmp_path / ".ai"
    result = scaffold_ai(root)

    assert set(result.created) == EXPECTED
    assert result.skipped == []
    for rel in EXPECTED:
        assert (root / rel).is_file()


def test_scaffold_is_idempotent(tmp_path: Path):
    root = tmp_path / ".ai"
    scaffold_ai(root)
    second = scaffold_ai(root)

    assert second.created == []
    assert set(second.skipped) == EXPECTED


def test_scaffold_infers_project_name(tmp_path: Path):
    project_dir = tmp_path / "my-project"
    project_dir.mkdir()
    root = project_dir / ".ai"
    scaffold_ai(root)

    repo_yml = (root / "repo.yml").read_text(encoding="utf-8")
    assert "name: my-project" in repo_yml
    assert "name: TODO" not in repo_yml


def test_scaffold_keeps_user_edits(tmp_path: Path):
    root = tmp_path / ".ai"
    scaffold_ai(root)
    (root / "repo.yml").write_text("name: my-edited-repo\n", encoding="utf-8")

    scaffold_ai(root)

    assert (root / "repo.yml").read_text(encoding="utf-8") == "name: my-edited-repo\n"
