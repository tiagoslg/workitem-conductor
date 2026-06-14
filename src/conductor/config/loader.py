"""Loading and validating ``.ai/repo.yml``."""

from __future__ import annotations

import yaml

from ..paths import AiPaths
from .models import RepoConfig


class RepoConfigError(Exception):
    """Raised when repo.yml is present but malformed."""


def load_repo_config(paths: AiPaths) -> RepoConfig:
    """Return the repo config, or sensible defaults when repo.yml is absent.

    An absent file is fine (defaults, dry-run everywhere). A present-but-invalid
    file is an error — we don't want to silently ignore a misconfigured backend.
    """
    path = paths.repo_config
    if not path.is_file():
        return RepoConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        return RepoConfig.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError and friends
        raise RepoConfigError(f"Invalid {path}: {exc}") from exc
