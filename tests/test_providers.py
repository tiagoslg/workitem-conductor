import io
import json
import sys
from pathlib import Path
from urllib import error

import pytest

from conductor.config.loader import RepoConfigError, load_repo_config
from conductor.config.models import ProviderConfig, RepoConfig, RoleBinding
from conductor.paths import AiPaths
from conductor.providers import api as api_module
from conductor.providers.api import ApiProvider
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


# --- api provider (stubbed transport, no network) -----------------------

class _FakeResp:
    """Minimal stand-in for the urlopen context manager."""

    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _api_provider() -> ApiProvider:
    return ApiProvider(
        name="qwen", model="qwen2.5", base_url="https://host/v1", api_key_env="TEST_API_KEY"
    )


def _request(prompt: str = "do the thing") -> ProviderRequest:
    return ProviderRequest(role="refiner", prompt=prompt, workitem_id="wi", cwd=Path("."))


def test_build_provider_api():
    provider = build_provider(
        "qwen",
        ProviderConfig(type="api", base_url="https://host/v1", model="m", api_key_env="K"),
    )
    assert isinstance(provider, ApiProvider)
    assert provider.model == "m"
    assert provider.base_url == "https://host/v1"


def test_build_provider_api_missing_fields():
    with pytest.raises(ProviderConfigError) as exc:
        build_provider("qwen", ProviderConfig(type="api"))
    message = str(exc.value)
    assert "base_url" in message and "model" in message and "api_key_env" in message


def test_api_available_reflects_env(monkeypatch):
    provider = _api_provider()
    monkeypatch.delenv("TEST_API_KEY", raising=False)
    assert provider.available() is False
    monkeypatch.setenv("TEST_API_KEY", "secret")
    assert provider.available() is True


def test_api_run_success_builds_request(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "secret")
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["body"] = json.loads(req.data)
        captured["timeout"] = timeout
        return _FakeResp(json.dumps({"choices": [{"message": {"content": "hi there"}}]}))

    monkeypatch.setattr(api_module.request, "urlopen", fake_urlopen)

    result = _api_provider().run(_request("do the thing"))

    assert result.ok
    assert result.output == "hi there"
    assert result.provider == "qwen"
    assert captured["url"] == "https://host/v1/chat/completions"
    assert captured["auth"] == "Bearer secret"
    assert captured["body"]["model"] == "qwen2.5"
    assert captured["body"]["messages"] == [{"role": "user", "content": "do the thing"}]


def test_api_run_missing_key_does_not_call_network(monkeypatch):
    monkeypatch.delenv("TEST_API_KEY", raising=False)
    called = {"hit": False}

    def fake_urlopen(req, timeout=None):  # pragma: no cover - must not run
        called["hit"] = True
        return _FakeResp("{}")

    monkeypatch.setattr(api_module.request, "urlopen", fake_urlopen)
    result = _api_provider().run(_request())

    assert not result.ok
    assert "not set" in result.error
    assert called["hit"] is False


def test_api_run_http_error(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "secret")

    def fake_urlopen(req, timeout=None):
        raise error.HTTPError(
            req.full_url, 401, "Unauthorized", None, io.BytesIO(b'{"error":"bad key"}')
        )

    monkeypatch.setattr(api_module.request, "urlopen", fake_urlopen)
    result = _api_provider().run(_request())

    assert not result.ok
    assert "401" in result.error


def test_api_run_unparseable_response(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "secret")
    monkeypatch.setattr(
        api_module.request, "urlopen", lambda req, timeout=None: _FakeResp("not json at all")
    )
    result = _api_provider().run(_request())
    assert not result.ok
    assert "unparseable" in result.error


def test_api_run_missing_choices(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "secret")
    monkeypatch.setattr(
        api_module.request,
        "urlopen",
        lambda req, timeout=None: _FakeResp(json.dumps({"unexpected": True})),
    )
    result = _api_provider().run(_request())
    assert not result.ok
    assert "unparseable" in result.error
