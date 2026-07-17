"""The ``summarizer`` role: curates ``memory.yml`` so later prompts don't need
to resend every prior raw output (see ``core/context.py``).

Like the reviewer's ``REVIEW:`` verdict and the refiner's ``QUESTIONS:``/
``CONTRACT:`` gate, the summarizer is driven by a single marker line:

    SUMMARY:
    ```yaml
    current_summary: >
      ...
    open_issues: []
    decisions: []
    resolved_issues: []
    ```

No marker (e.g. an unbound role falling back to the dry-run provider) means
"nothing to update" — not an error. Summarization is never load-bearing: a
failing or unbound summarizer must never break `execute`/`reopen`.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..paths import AiPaths
from ..providers.base import Provider, ProviderRequest
from ..workitems.manager import Workitem, load_memory, save_memory
from ..workitems.models import Decision, MemoryRecord, ValidationStatus
from .context import build_summarizer_context
from .runs import StepRecord
from .yaml_utils import extract_fenced_yaml

# Same leniency as refine.py's CONTRACT:/QUESTIONS: markers — tolerates
# markdown decoration and a dropped colon.
_SUMMARY_RE = re.compile(r"(?im)^[ \t>#*`]*SUMMARY\b[ \t]*:?")


def parse_summary(output: str) -> dict | None:
    """Extract the fenced YAML block after the ``SUMMARY:`` marker, or ``None``."""
    m = _SUMMARY_RE.search(output)
    if not m:
        return None
    return extract_fenced_yaml(output[m.end():])


def merge_memory(existing: MemoryRecord, parsed: dict) -> MemoryRecord:
    """Merge a freshly parsed summary into ``existing`` memory.

    ``current_summary``/``open_issues`` represent *current* state and are
    replaced wholesale; ``decisions``/``resolved_issues`` are append-only,
    skipping exact-text duplicates so repeated summarizer calls (e.g. one per
    fix-loop round) don't pile up the same entry.
    """
    merged = existing.model_copy(deep=True)

    current_summary = parsed.get("current_summary")
    if current_summary:
        merged.current_summary = str(current_summary).strip()

    open_issues = parsed.get("open_issues")
    if open_issues is not None:
        merged.open_issues = [str(i).strip() for i in open_issues if str(i).strip()]

    existing_decisions = {d.decision for d in merged.decisions}
    for item in parsed.get("decisions") or []:
        if isinstance(item, dict):
            text = str(item.get("decision", "")).strip()
            by = str(item.get("by") or "summarizer")
        else:
            text = str(item).strip()
            by = "summarizer"
        if text and text not in existing_decisions:
            merged.decisions.append(Decision(decision=text, by=by))
            existing_decisions.add(text)

    existing_resolved = set(merged.resolved_issues)
    for item in parsed.get("resolved_issues") or []:
        text = str(item).strip()
        if text and text not in existing_resolved:
            merged.resolved_issues.append(text)
            existing_resolved.add(text)

    validation_status = parsed.get("validation_status")
    if isinstance(validation_status, dict):
        merged.validation_status = ValidationStatus(
            last_tests=[str(t) for t in (validation_status.get("last_tests") or [])],
            failing=[str(t) for t in (validation_status.get("failing") or [])],
        )

    return merged


def summarize(
    paths: AiPaths,
    wi: Workitem,
    provider: Provider,
    trigger: str,
    run_steps: list[StepRecord],
    execution_cwd: Path | None,
) -> MemoryRecord | None:
    """Call the summarizer and persist its output, or return ``None`` if it
    made no proposal (no marker) or the provider call failed.
    """
    prompt = build_summarizer_context(paths, wi, trigger, run_steps, execution_cwd)
    result = provider.run(
        ProviderRequest(
            role="summarizer",
            prompt=prompt,
            workitem_id=wi.workitem_id,
            cwd=execution_cwd or paths.cwd,
        )
    )
    if not result.ok:
        return None
    parsed = parse_summary(result.output)
    if parsed is None:
        return None
    merged = merge_memory(load_memory(paths, wi.workitem_id), parsed)
    save_memory(paths, wi.workitem_id, merged)
    return merged
