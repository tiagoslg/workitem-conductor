"""Shared test fixtures.

Isolates every test from the developer's real conductor home directories.
Without this, any test that touches ``AiPaths.workitems_dir``/``worktrees_dir``
(now centralized under ``data_home()``, see ``paths.py``) would write into the
real ``~/.local/share/conductor/...`` instead of a throwaway tmp dir.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_conductor_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONDUCTOR_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("CONDUCTOR_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("CONDUCTOR_CACHE_HOME", str(tmp_path / "cache"))
