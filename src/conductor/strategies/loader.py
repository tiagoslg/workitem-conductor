"""Loading strategy definitions from the repository's ``.ai/strategies/`` directory."""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from ..paths import AiPaths
from .models import Strategy


class StrategyNotFound(Exception):
    def __init__(self, name: str, path: str) -> None:
        super().__init__(f"Strategy '{name}' not found at {path}")
        self.name = name


def strategy_path(paths: AiPaths, name: str) -> Path:
    return paths.strategies_dir / f"{name}.yml"


def load_strategy(paths: AiPaths, name: str) -> Strategy:
    """Load and validate the strategy named ``name`` (without the ``.yml`` suffix)."""
    path = strategy_path(paths, name)
    if not path.is_file():
        raise StrategyNotFound(name, str(path))
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data.setdefault("name", name)
    return Strategy.model_validate(data)


def strategy_content_hash(paths: AiPaths, name: str) -> str:
    """Short digest of the strategy file's raw content, for run.yml's audit trail.

    Same digest pattern as ``paths.py::_project_id`` — lets a run record
    *which version* of a strategy was in effect, since the file can be
    edited after a workitem started using it.
    """
    return hashlib.sha1(strategy_path(paths, name).read_bytes()).hexdigest()[:8]
