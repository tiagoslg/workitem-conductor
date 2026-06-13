"""A provider that performs no real model call.

It echoes a deterministic placeholder so the full loop — flow selection, context
building, state transitions, artifact writing, final report — can be exercised
and tested before any real backend is wired in. This is the backbone of
``conductor execute`` in MVP 2 slice 1.
"""

from __future__ import annotations

from .base import Provider, ProviderRequest, ProviderResult


class DryRunProvider(Provider):
    name = "dry_run"

    def run(self, request: ProviderRequest) -> ProviderResult:
        output = (
            f"# [dry-run] {request.role}\n\n"
            f"No model was called. A real provider would act on the prompt below "
            f"and return its work product here.\n\n"
            f"<details>\n<summary>prompt sent to the {request.role}</summary>\n\n"
            f"```\n{request.prompt}\n```\n\n</details>\n"
        )
        return ProviderResult(ok=True, output=output, provider=self.name)
