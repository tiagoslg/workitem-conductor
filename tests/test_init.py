from pathlib import Path

from typer.testing import CliRunner

from conductor.cli import app
from conductor.scaffold import scaffold_ai

runner = CliRunner()

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


# ---------------------------------------------------------------------------
# conductor init — gitignore integration
# ---------------------------------------------------------------------------

def test_init_creates_gitignore_with_ai_entry(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    gitignore = tmp_path / ".gitignore"
    assert gitignore.is_file()
    assert ".ai/" in gitignore.read_text()
    assert "created" in result.output or "added" in result.output


def test_init_appends_to_existing_gitignore(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")

    runner.invoke(app, ["init"])

    content = (tmp_path / ".gitignore").read_text()
    assert "node_modules/" in content
    assert ".ai/" in content


def test_init_does_not_duplicate_ai_entry(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gitignore").write_text(".ai/\n", encoding="utf-8")

    result = runner.invoke(app, ["init"])

    content = (tmp_path / ".gitignore").read_text()
    assert content.count(".ai/") == 1
    assert "added" not in result.output and "created" not in result.output


def test_init_recognises_ai_without_trailing_slash(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gitignore").write_text(".ai\n", encoding="utf-8")

    result = runner.invoke(app, ["init"])

    content = (tmp_path / ".gitignore").read_text()
    assert content.count(".ai") == 1
