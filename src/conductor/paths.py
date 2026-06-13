"""Locating and addressing the local ``.ai/`` directory.

The conductor is installed once but used from inside a target repository. All
state lives under a ``.ai/`` directory at the repo (or workspace) root. These
helpers find that directory — walking up from the current working directory so
the CLI also works when invoked from a subdirectory — and expose the well-known
paths inside it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

AI_DIRNAME = ".ai"


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
    def workitems_dir(self) -> Path:
        return self.root / "workitems"

    @property
    def active_pointer(self) -> Path:
        return self.root / "active_workitem.txt"

    def workitem_dir(self, workitem_id: str) -> Path:
        return self.workitems_dir / workitem_id


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
