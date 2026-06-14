"""Resolve a role to the provider instance that should execute it.

The engine asks for a provider by role name; the registry answers from
``repo.yml``. Roles without a binding fall back to dry-run, so a freshly
initialised repo (empty ``providers``/``roles``) runs end-to-end out of the box.
``dry_run=True`` forces dry-run for every role regardless of config.
"""

from __future__ import annotations

from collections.abc import Callable

from ..config.models import ProviderConfig, RepoConfig
from .api import ApiProvider
from .base import Provider
from .cli_one_shot import CliOneShotProvider
from .dryrun import DryRunProvider
from .ollama import OllamaProvider

ProviderFor = Callable[[str], Provider]


class ProviderConfigError(Exception):
    """Raised when a role/provider binding cannot be resolved or built."""


def build_provider(name: str, config: ProviderConfig) -> Provider:
    """Instantiate a provider from its config block."""
    if config.type == "dry_run":
        return DryRunProvider()
    if config.type == "cli_one_shot":
        if not config.command:
            raise ProviderConfigError(
                f"provider '{name}' is type cli_one_shot but has no 'command'"
            )
        return CliOneShotProvider(
            name=name,
            command=config.command,
            args=config.args,
            prompt_via=config.prompt_via,
            timeout=config.timeout,
        )
    if config.type == "api":
        missing = [
            field
            for field in ("base_url", "model", "api_key_env")
            if not getattr(config, field)
        ]
        if missing:
            raise ProviderConfigError(
                f"provider '{name}' is type api but missing: {', '.join(missing)}"
            )
        return ApiProvider(
            name=name,
            model=config.model,
            base_url=config.base_url,
            api_key_env=config.api_key_env,
            timeout=config.timeout,
        )
    if config.type == "ollama":
        if not config.model:
            raise ProviderConfigError(
                f"provider '{name}' is type ollama but has no 'model'"
            )
        return OllamaProvider(
            name=name,
            model=config.model,
            base_url=config.base_url or OllamaProvider.DEFAULT_BASE_URL,
            timeout=config.timeout,
        )
    raise ProviderConfigError(
        f"provider '{name}' has unsupported type '{config.type}'"
    )


def build_provider_for(config: RepoConfig, dry_run: bool = False) -> ProviderFor:
    """Return a ``role -> Provider`` resolver, caching one instance per provider."""
    fallback = DryRunProvider()
    cache: dict[str, Provider] = {}

    def resolve(role: str) -> Provider:
        if dry_run:
            return fallback
        binding = config.roles.get(role)
        if binding is None:
            return fallback
        provider_name = binding.provider
        if provider_name not in cache:
            provider_config = config.providers.get(provider_name)
            if provider_config is None:
                raise ProviderConfigError(
                    f"role '{role}' is bound to unknown provider '{provider_name}'"
                )
            cache[provider_name] = build_provider(provider_name, provider_config)
        return cache[provider_name]

    return resolve
