"""The provider adapter interface.

A provider is *how* a role is executed: a CLI run headlessly, an API call, a
local model. The core engine depends only on this interface, never on a concrete
backend — so a role can be re-pointed from one provider to another without
touching the flow or the engine.

The conductor does **not** own provider authentication. CLI providers assume the
underlying CLI (Codex, Claude, …) is already logged in on the machine.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProviderRequest:
    """Everything a provider needs to execute one role step."""

    role: str
    prompt: str
    workitem_id: str
    cwd: Path


@dataclass
class ProviderResult:
    """The outcome of a provider run."""

    ok: bool
    output: str
    provider: str
    error: str | None = None


class Provider(ABC):
    """Base class for all execution backends."""

    #: short, stable identifier used in state/logs (e.g. "dry_run", "codex_cli")
    name: str = "provider"

    @abstractmethod
    def run(self, request: ProviderRequest) -> ProviderResult:
        """Execute one role step and return its result."""
        raise NotImplementedError

    def available(self) -> bool:
        """Whether this provider can run right now (CLI present, key set, …)."""
        return True
