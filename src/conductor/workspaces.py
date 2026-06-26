"""The machine-global workspace registry.

The conductor is local-per-repo: state lives in each project's ``.ai/``. The
registry adds a thin, *global* index — a list of project roots, grouped into
named workspaces (the VS Code sense) — so a tool like the dashboard can see work
across several projects at once. It holds **paths only**; it never duplicates or
owns project state.

The file lives under a config home resolved as: ``CONDUCTOR_CONFIG_HOME`` (used
by tests) → ``XDG_CONFIG_HOME`` → ``~/.config``, then ``conductor/workspaces.yml``.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

DEFAULT_WORKSPACE = "default"


class WorkspaceRegistryError(Exception):
    """Raised when the registry file exists but cannot be parsed."""


class Workspace(BaseModel):
    paths: list[str] = Field(default_factory=list)


class WorkspaceRegistry(BaseModel):
    workspaces: dict[str, Workspace] = Field(
        default_factory=lambda: {DEFAULT_WORKSPACE: Workspace()}
    )

    def workspace(self, name: str) -> Workspace:
        """Return the named workspace, creating an empty one if absent."""
        return self.workspaces.setdefault(name, Workspace())


def config_home() -> Path:
    """Resolve the config home directory (test-overridable)."""
    override = os.environ.get("CONDUCTOR_CONFIG_HOME")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg)
    return Path.home() / ".config"


def registry_path() -> Path:
    return config_home() / "conductor" / "workspaces.yml"


def global_defaults_path() -> Path:
    """Path to the machine-global provider/role defaults (~/.config/conductor/defaults.yml)."""
    return config_home() / "conductor" / "defaults.yml"


def load_registry() -> WorkspaceRegistry:
    """Load the registry, or return defaults when the file is absent."""
    path = registry_path()
    if not path.exists():
        return WorkspaceRegistry()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return WorkspaceRegistry.model_validate(data)
    except (yaml.YAMLError, ValidationError) as exc:
        raise WorkspaceRegistryError(f"invalid registry at {path}: {exc}") from exc


def save_registry(registry: WorkspaceRegistry) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(registry.model_dump(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _resolve(path: Path | str) -> str:
    return str(Path(path).expanduser().resolve())


def add_project(
    registry: WorkspaceRegistry, path: Path | str, workspace: str = DEFAULT_WORKSPACE
) -> tuple[str, bool, bool]:
    """Register ``path`` under ``workspace``.

    Returns ``(resolved_path, added, has_ai)`` where ``added`` is False when the
    path was already registered there, and ``has_ai`` reports whether the path
    currently contains an ``.ai/`` directory (a warning hint, not a rejection —
    the project may be ``conductor init``-ed later).
    """
    resolved = _resolve(path)
    ws = registry.workspace(workspace)
    added = resolved not in ws.paths
    if added:
        ws.paths.append(resolved)
    has_ai = (Path(resolved) / ".ai").is_dir()
    return resolved, added, has_ai


def remove_project(
    registry: WorkspaceRegistry, path: Path | str, workspace: str | None = None
) -> bool:
    """Remove ``path`` from one workspace (or all when ``workspace`` is None)."""
    resolved = _resolve(path)
    names = [workspace] if workspace else list(registry.workspaces)
    removed = False
    for name in names:
        ws = registry.workspaces.get(name)
        if ws and resolved in ws.paths:
            ws.paths.remove(resolved)
            removed = True
    return removed


def workspace_dir(name: str) -> Path:
    """Return the directory for a named workspace's own state (config, workitems)."""
    return config_home() / "conductor" / "workspaces" / name


def load_workspace_paths(name: str) -> "WorkspacePaths":
    """Resolve a named workspace into a WorkspacePaths ready to use."""
    from .paths import WorkspacePaths

    registry = load_registry()
    ws = registry.workspaces.get(name)
    project_roots = [Path(p) for p in (ws.paths if ws else [])]
    return WorkspacePaths(root=workspace_dir(name), name=name, project_roots=project_roots)


def list_projects(
    registry: WorkspaceRegistry, workspace: str | None = None
) -> list[str]:
    """All project paths in one workspace, or across all (deduped, sorted)."""
    if workspace is not None:
        ws = registry.workspaces.get(workspace)
        return list(ws.paths) if ws else []
    seen: set[str] = set()
    for ws in registry.workspaces.values():
        seen.update(ws.paths)
    return sorted(seen)
