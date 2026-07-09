"""Models for strategy definitions loaded from ``.ai/strategies/<name>.yml``.

A strategy is a named bundle of a flow choice plus optional overlays on top
of ``repo.yml``'s role bindings, context budget, and fix-loop cap. Everything
here is optional except ``flow`` — an empty ``roles``/unset ``context``/unset
``max_fix_iterations`` means "inherit repo.yml's value", so a strategy only
needs to declare what it wants to change. Provider *definitions*
(type/command/args/...) are never part of a strategy — only which provider a
role is *bound* to can be overridden; the definitions stay solely in
``repo.yml``'s ``providers:`` dict.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..config.models import ContextConfig, RoleBinding


class Strategy(BaseModel):
    name: str
    flow: str
    description: str = ""
    roles: dict[str, RoleBinding] = Field(default_factory=dict)
    context: ContextConfig | None = None
    max_fix_iterations: int | None = None
