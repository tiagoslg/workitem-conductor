"""The three XDG-style bases the conductor stores state under.

- ``config_home()``  — base for ``.../conductor/`` — curated, user-edited
  config (the workspace registry, global provider/role defaults).
- ``data_home()``    — base for ``.../conductor/`` — runtime state the
  conductor itself produces and grows over time (workitems, worktrees, runs).
- ``cache_home()``   — base for ``.../conductor/`` — disposable, safe to delete.

Each is the *base* directory (e.g. ``~/.config``, not ``~/.config/conductor``)
so callers append ``"conductor"`` themselves — matching the pre-existing
``config_home()`` contract used by the workspace registry. Resolution order:
an explicit ``CONDUCTOR_*_HOME`` override (used by tests), then the matching
``XDG_*_HOME``, then the POSIX default.
"""

from __future__ import annotations

import os
from pathlib import Path


def _home(explicit_env: str, xdg_env: str, default: Path) -> Path:
    override = os.environ.get(explicit_env)
    if override:
        return Path(override)
    xdg = os.environ.get(xdg_env)
    if xdg:
        return Path(xdg)
    return default


def config_home() -> Path:
    return _home("CONDUCTOR_CONFIG_HOME", "XDG_CONFIG_HOME", Path.home() / ".config")


def data_home() -> Path:
    return _home("CONDUCTOR_DATA_HOME", "XDG_DATA_HOME", Path.home() / ".local" / "share")


def cache_home() -> Path:
    return _home("CONDUCTOR_CACHE_HOME", "XDG_CACHE_HOME", Path.home() / ".cache")
