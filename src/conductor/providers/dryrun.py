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
        # Deliberately does not echo the prompt: the prompt is already saved
        # alongside as the step's .prompt.md, and echoing the reviewer's role
        # instructions (which contain REVIEW: examples) would confuse the gate.
        output = (
            f"# [dry-run] {request.role}\n\n"
            f"No model was called. A real provider would act on this role's prompt "
            f"(see the matching `.prompt.md`) and return its work product here.\n"
        )
        return ProviderResult(ok=True, output=output, provider=self.name)
