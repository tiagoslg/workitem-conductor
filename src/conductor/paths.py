"""Locating and addressing the local ``.ai/`` directory.

The conductor is installed once but used from inside a target repository. All
state lives under a ``.ai/`` directory at the repo (or workspace) root. These
helpers find that directory — walking up from the current working directory so
the CLI also works when invoked from a subdirectory — and expose the well-known
paths inside it.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .home import data_home

AI_DIRNAME = ".ai"


def _slug(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text or "project"


def _project_id(repo_root: Path) -> str:
    """A stable, human-legible id for a repo's central data directory.

    Derived from the resolved absolute path, so it stays stable across
    invocations from different cwd/symlinks but changes if the repo is moved —
    acceptable since there is no cross-path migration for runtime state.
    """
    resolved = str(repo_root.resolve())
    digest = hashlib.sha1(resolved.encode()).hexdigest()[:8]
    return f"{_slug(repo_root.name)}-{digest}"


class AiRootNotFound(Exception):
    """Raised when no ``.ai/`` directory can be located.

    Carries a user-facing hint so the CLI can present a clean message instead
    of a traceback.
    """

    def __init__(self) -> None:
        super().__init__(
            "No .ai/ directory found in this repository.\n"
            "Run `conductor init` first."
        )


@dataclass(frozen=True)
class AiPaths:
    """Well-known paths inside a located ``.ai/`` directory."""

    root: Path

    @property
    def cwd(self) -> Path:
        """Working directory for provider invocations (the repo root)."""
        return self.root.parent

    @property
    def repo_config(self) -> Path:
        return self.root / "repo.yml"

    @property
    def instructions(self) -> Path:
        return self.root / "instructions.md"

    @property
    def flows_dir(self) -> Path:
        return self.root / "flows"

    @property
    def roles_dir(self) -> Path:
        return self.root / "roles"

    @property
    def data_dir(self) -> Path:
        """Central runtime storage for this project, outside the git-tracked repo.

        Keyed by a project id derived from the repo's resolved path (see
        ``_project_id``), under ``data_home()/projects/<id>/``. ``.ai/`` holds
        only versionable config (repo.yml, instructions.md, flows/, roles/);
        everything the conductor produces at runtime lives here instead.
        """
        return data_home() / "conductor" / "projects" / _project_id(self.cwd)

    @property
    def workitems_dir(self) -> Path:
        return self.data_dir / "workitems"

    @property
    def active_pointer(self) -> Path:
        return self.data_dir / "active_workitem.txt"

    def workitem_dir(self, workitem_id: str) -> Path:
        return self.workitems_dir / workitem_id

    @property
    def worktrees_dir(self) -> Path:
        return self.data_dir / "worktrees"

    def worktree_dir(self, workitem_id: str) -> Path:
        return self.worktrees_dir / workitem_id


def find_ai_root(start: Path | None = None) -> Path | None:
    """Return the nearest ``.ai/`` directory at or above ``start``.

    Walks upward from ``start`` (default: current working directory) to the
    filesystem root. Returns ``None`` if no ``.ai/`` directory is found.
    """
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / AI_DIRNAME
        if candidate.is_dir():
            return candidate
    return None


def require_ai_paths(start: Path | None = None) -> AiPaths:
    """Locate the ``.ai/`` directory or raise :class:`AiRootNotFound`."""
    root = find_ai_root(start)
    if root is None:
        raise AiRootNotFound()
    return AiPaths(root=root)


@dataclass(frozen=True)
class WorkspacePaths:
    """Well-known paths for a named workspace.

    ``root`` (``~/.config/conductor/workspaces/<name>/``) holds curated,
    versionable-ish config: ``config.yml``, ``instructions.md``, ``roles/``,
    ``flows/`` — the workspace-level equivalent of a repo's ``.ai/``. Runtime
    state (workitems, the active-workitem pointer) is *not* under ``root`` —
    it lives under the central data home instead (``data_dir``, mirroring
    ``AiPaths.data_dir`` for single-repo projects), keyed by workspace name.
    """

    root: Path          # ~/.config/conductor/workspaces/<name>/
    name: str
    project_roots: list[Path] = field(default_factory=list)

    @property
    def config(self) -> Path:
        return self.root / "config.yml"

    @property
    def instructions(self) -> Path:
        return self.root / "instructions.md"

    @property
    def roles_dir(self) -> Path:
        return self.root / "roles"

    @property
    def data_dir(self) -> Path:
        return data_home() / "conductor" / "workspaces" / self.name

    @property
    def workitems_dir(self) -> Path:
        return self.data_dir / "workitems"

    @property
    def active_pointer(self) -> Path:
        return self.data_dir / "active_workitem.txt"

    def workitem_dir(self, workitem_id: str) -> Path:
        return self.workitems_dir / workitem_id

    @property
    def flows_dir(self) -> Path:
        return self.root / "flows"

    @property
    def cwd(self) -> Path:
        """Common ancestor of all project roots — lets a CLI provider navigate both repos."""
        if not self.project_roots:
            return Path.home()
        try:
            return Path(os.path.commonpath([str(p) for p in self.project_roots]))
        except ValueError:
            return Path.home()
