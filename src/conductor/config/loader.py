"""Loading and validating ``.ai/repo.yml`` and workspace ``config.yml``.

Config cascade: global defaults → repo/workspace config.
Providers and roles defined in the local file take precedence; anything not
overridden falls back to the global defaults at
``~/.config/conductor/defaults.yml``.
"""

from __future__ import annotations

import yaml

from ..paths import AiPaths
from ..workspaces import global_defaults_path
from .models import ProviderConfig, RepoConfig, RoleBinding


class RepoConfigError(Exception):
    """Raised when repo.yml / config.yml is present but malformed."""


def _parse_config(path) -> RepoConfig:
    """Parse one config file; return empty RepoConfig when absent."""
    if not path.is_file():
        return RepoConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        return RepoConfig.model_validate(raw)
    except Exception as exc:
        raise RepoConfigError(f"Invalid {path}: {exc}") from exc


def load_global_defaults() -> RepoConfig:
    """Return the machine-global defaults, or an empty config when unset."""
    try:
        return _parse_config(global_defaults_path())
    except RepoConfigError:
        return RepoConfig()


def _merge(base: RepoConfig, override: RepoConfig) -> RepoConfig:
    """Merge two configs: ``override`` wins; ``base`` fills gaps.

    Providers and roles defined in ``override`` replace the base entries of
    the same name. Entries only in ``base`` are kept. The repo name and
    default_flow come from ``override`` when set to non-defaults, else ``base``.
    """
    merged_providers = {**base.providers, **override.providers}
    merged_roles = {**base.roles, **override.roles}
    return RepoConfig(
        name=override.name if override.name != "TODO" else base.name,
        default_flow=override.default_flow,
        providers=merged_providers,
        roles=merged_roles,
        refine=override.refine,
        source_branch=override.source_branch if override.source_branch is not None else base.source_branch,
        target_branch=override.target_branch if override.target_branch is not None else base.target_branch,
    )


def load_repo_config(paths: AiPaths) -> RepoConfig:
    """Return the repo config merged over the global defaults."""
    defaults = load_global_defaults()
    local = _parse_config(paths.repo_config)
    return _merge(defaults, local)


def load_workspace_config(ws_paths) -> RepoConfig:
    """Return the workspace config merged over the global defaults."""
    defaults = load_global_defaults()
    local = _parse_config(ws_paths.config)
    return _merge(defaults, local)


def save_global_defaults(config: RepoConfig) -> None:
    """Write provider + role sections of ``config`` to the global defaults file."""
    path = global_defaults_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if config.providers:
        data["providers"] = {
            name: _provider_to_dict(p) for name, p in config.providers.items()
        }
    if config.roles:
        data["roles"] = {name: {"provider": rb.provider} for name, rb in config.roles.items()}
    path.write_text(
        "# workitem-conductor global defaults\n"
        "# Inherited by every repo and workspace unless overridden locally.\n"
        + yaml.dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )


def _provider_to_dict(p: ProviderConfig) -> dict:
    d: dict = {"type": p.type}
    if p.command:
        d["command"] = p.command
    if p.args:
        d["args"] = p.args
    if p.prompt_via != "stdin":
        d["prompt_via"] = p.prompt_via
    if p.model:
        d["model"] = p.model
    if p.base_url:
        d["base_url"] = p.base_url
    if p.api_key_env:
        d["api_key_env"] = p.api_key_env
    if p.timeout != 600:
        d["timeout"] = p.timeout
    return d
