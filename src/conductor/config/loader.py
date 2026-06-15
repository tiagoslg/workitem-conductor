"""Loading and validating ``.ai/repo.yml`` and workspace ``config.yml``."""

from __future__ import annotations

import yaml

from ..paths import AiPaths
from .models import RepoConfig


class RepoConfigError(Exception):
    """Raised when repo.yml / config.yml is present but malformed."""


def _load_config_from_path(path) -> RepoConfig:
    """Shared loader for repo.yml and workspace config.yml (same schema)."""
    if not path.is_file():
        return RepoConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        return RepoConfig.model_validate(raw)
    except Exception as exc:
        raise RepoConfigError(f"Invalid {path}: {exc}") from exc


def load_repo_config(paths: AiPaths) -> RepoConfig:
    """Return the repo config, or sensible defaults when repo.yml is absent."""
    return _load_config_from_path(paths.repo_config)


def load_workspace_config(ws_paths) -> RepoConfig:
    """Return the workspace config, or sensible defaults when config.yml is absent."""
    return _load_config_from_path(ws_paths.config)
