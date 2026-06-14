import sys
from pathlib import Path

import pytest

from conductor.config.loader import RepoConfigError, load_repo_config
from conductor.config.models import ProviderConfig, RepoConfig, RoleBinding
from conductor.paths import AiPaths
from conductor.providers.base import ProviderRequest
from conductor.providers.cli_one_shot import CliOneShotProvider
from conductor.providers.dryrun import DryRunProvider
from conductor.providers.registry import (
    ProviderConfigError,
    build_provider,
    build_provider_for,
)
from conductor.scaffold import scaffold_ai


@pytest.fixture
def paths(tmp_path: Path) -> AiPaths:
    root = tmp_path / ".ai"
    scaffold_ai(root)
    return AiPaths(root=root)


# --- config loader -------------------------------------------------------

def test_load_repo_config_defaults_when_absent(tmp_path: Path):
    paths = AiPaths(root=tmp_path / ".ai")
    (tmp_path / ".ai").mkdir()
    config = load_repo_config(paths)
    assert config.default_flow == "simple-change"
    assert config.providers == {}
    assert config.roles == {}


def test_load_repo_config_parses_bindings(paths: AiPaths):
    paths.repo_config.write_text(
        "name: svc\n"
        "providers:\n"
        "  codex_cli: { type: cli_one_shot, command: codex }\n"
        "roles:\n"
        "  implementer: { provider: codex_cli }\n",
        encoding="utf-8",
    )
    config = load_repo_config(paths)
    assert config.roles["implementer"].provider == "codex_cli"
    assert config.providers["codex_cli"].type == "cli_one_shot"
    assert config.providers["codex_cli"].command == "codex"


def test_load_repo_config_rejects_invalid(paths: AiPaths):
    paths.repo_config.write_text(
        "providers:\n  bad: { type: not_a_real_type }\n", encoding="utf-8"
    )
    with pytest.raises(RepoConfigError):
        load_repo_config(paths)


# --- registry ------------------------------------------------------------

def test_build_provider_cli_one_shot():
    provider = build_provider("codex_cli", ProviderConfig(type="cli_one_shot", command="codex"))
    assert isinstance(provider, CliOneShotProvider)
    assert provider.command == "codex"


def test_build_provider_requires_command():
    with pytest.raises(ProviderConfigError):
        build_provider("broken", ProviderConfig(type="cli_one_shot"))


def test_provider_for_falls_back_to_dry_run_when_unbound():
    config = RepoConfig()
    resolve = build_provider_for(config)
    assert isinstance(resolve("planner"), DryRunProvider)


def test_provider_for_resolves_binding():
    config = RepoConfig(
        providers={"codex_cli": ProviderConfig(type="cli_one_shot", command="codex")},
        roles={"implementer": RoleBinding(provider="codex_cli")},
    )
    resolve = build_provider_for(config)
    impl = resolve("implementer")
    assert isinstance(impl, CliOneShotProvider)
    # planner is unbound -> dry run
    assert isinstance(resolve("planner"), DryRunProvider)
    # cached: same instance returned for the same provider
    assert resolve("implementer") is impl


def test_provider_for_dry_run_overrides_everything():
    config = RepoConfig(
        providers={"codex_cli": ProviderConfig(type="cli_one_shot", command="codex")},
        roles={"implementer": RoleBinding(provider="codex_cli")},
    )
    resolve = build_provider_for(config, dry_run=True)
    assert isinstance(resolve("implementer"), DryRunProvider)


def test_provider_for_unknown_provider_raises():
    config = RepoConfig(roles={"implementer": RoleBinding(provider="ghost")})
    resolve = build_provider_for(config)
    with pytest.raises(ProviderConfigError):
        resolve("implementer")


# --- cli_one_shot provider (real subprocess, no model) -------------------

def test_cli_one_shot_runs_via_stdin(tmp_path: Path):
    # Use the current Python as a stand-in CLI that echoes stdin.
    provider = CliOneShotProvider(
        name="echo_cli",
        command=sys.executable,
        args=["-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
        prompt_via="stdin",
    )
    result = provider.run(
        ProviderRequest(role="planner", prompt="hello", workitem_id="wi", cwd=tmp_path)
    )
    assert result.ok
    assert result.output == "HELLO"


def test_cli_one_shot_reports_nonzero_exit(tmp_path: Path):
    provider = CliOneShotProvider(
        name="failer",
        command=sys.executable,
        args=["-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
    )
    result = provider.run(
        ProviderRequest(role="planner", prompt="x", workitem_id="wi", cwd=tmp_path)
    )
    assert not result.ok
    assert "boom" in result.error


def test_cli_one_shot_missing_command(tmp_path: Path):
    provider = CliOneShotProvider(name="nope", command="definitely-not-a-real-cmd-xyz")
    assert provider.available() is False
    result = provider.run(
        ProviderRequest(role="planner", prompt="x", workitem_id="wi", cwd=tmp_path)
    )
    assert not result.ok
    assert "not found" in result.error
