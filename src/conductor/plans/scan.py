"""Scanning registered repos for ``.ai/execution_plans/*.md`` files."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ValidationError

from ..workspaces import list_projects, load_registry
from .frontmatter import parse_frontmatter
from .models import Plan, PlanFrontmatter


class ScanWarning(BaseModel):
    """A plan file that could not be parsed — surfaced, never silently dropped."""

    path: Path
    reason: str


def scan_repo(repo_root: Path, repo_name: str) -> tuple[list[Plan], list[ScanWarning]]:
    """Scan one repo's ``.ai/execution_plans/`` for plan files."""
    plans: list[Plan] = []
    warnings: list[ScanWarning] = []
    plans_dir = repo_root / ".ai" / "execution_plans"
    if not plans_dir.is_dir():
        return plans, warnings

    for path in sorted(plans_dir.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        data = parse_frontmatter(text)
        if data is None:
            warnings.append(ScanWarning(path=path, reason="no valid frontmatter block"))
            continue
        data.setdefault("primary_repo", repo_name)
        try:
            fm = PlanFrontmatter.model_validate(data)
        except ValidationError as exc:
            warnings.append(ScanWarning(path=path, reason=str(exc)))
            continue
        plans.append(Plan(frontmatter=fm, repo=repo_name, path=path))
    return plans, warnings


def scan_repos(repos: dict[str, Path]) -> tuple[list[Plan], list[ScanWarning]]:
    """Scan several named repos, aggregating plans and warnings."""
    all_plans: list[Plan] = []
    all_warnings: list[ScanWarning] = []
    for name, root in repos.items():
        plans, warnings = scan_repo(root, name)
        all_plans.extend(plans)
        all_warnings.extend(warnings)
    return all_plans, all_warnings


def resolve_repos(workspace: str | None = None, *, include_cwd: bool = True) -> dict[str, Path]:
    """Repo name -> resolved root, from the workspace registry plus (optionally) ``cwd``.

    Names default to the directory's basename; on a collision, the parent
    directory's name is prefixed to disambiguate.
    """
    registry = load_registry()
    raw_paths = [Path(p) for p in list_projects(registry, workspace)]
    if include_cwd:
        cwd = Path.cwd().resolve()
        if cwd not in raw_paths:
            raw_paths.append(cwd)

    repos: dict[str, Path] = {}
    for path in raw_paths:
        name = path.name
        if name in repos and repos[name] != path:
            name = f"{path.parent.name}/{path.name}"
        repos[name] = path
    return repos
