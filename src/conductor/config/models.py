"""Models for ``.ai/repo.yml`` — the versionable repo configuration.

This is where roles are bound to providers. A provider is declared once
(its type and how to invoke it) and referenced by name from each role, so the
same flow can run on different backends by editing config alone.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ProviderType = Literal["cli_one_shot", "dry_run", "api", "ollama"]
PromptVia = Literal["stdin", "arg"]


class ProviderConfig(BaseModel):
    """How to execute one provider backend."""

    type: ProviderType
    # cli_one_shot
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    prompt_via: PromptVia = "stdin"
    timeout: int = 600
    # api / ollama (declared now, wired in a later slice)
    model: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None


class RoleBinding(BaseModel):
    """Which provider a role runs on."""

    provider: str


class RefineConfig(BaseModel):
    """Settings for the AI-assisted goal-definition loop (``conductor refine``)."""

    max_question_rounds: int = 5


class ContextConfig(BaseModel):
    """Budget and section toggles for ``core/context.py::build_context``.

    Curated memory (``memory.yml``, written by the ``summarizer`` role) is the
    default context source; raw prior-step outputs are opt-in — flip
    ``include_raw_outputs`` on for a repo that doesn't yet trust its summarizer.
    """

    max_prompt_chars: int = 64_000
    include_raw_outputs: bool = False
    include_memory: bool = True
    include_last_diff: bool = True
    include_last_review: bool = True


class RepoConfig(BaseModel):
    name: str = "TODO"
    default_flow: str = "simple-change"
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    roles: dict[str, RoleBinding] = Field(default_factory=dict)
    refine: RefineConfig = Field(default_factory=RefineConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    source_branch: str | None = None
    target_branch: str | None = None
