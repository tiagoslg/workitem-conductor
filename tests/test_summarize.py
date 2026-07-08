"""Tests for the summarizer role: SUMMARY: marker parsing, memory merge, and
the end-to-end summarize() call."""

from pathlib import Path

import pytest

from conductor.core.summarize import merge_memory, parse_summary, summarize
from conductor.paths import AiPaths
from conductor.providers.base import ProviderRequest, ProviderResult
from conductor.providers.dryrun import DryRunProvider
from conductor.scaffold import scaffold_ai
from conductor.workitems.manager import approve_goal, create_workitem, load_memory
from conductor.workitems.models import Decision, MemoryRecord


@pytest.fixture
def paths(tmp_path: Path) -> AiPaths:
    root = tmp_path / ".ai"
    scaffold_ai(root)
    return AiPaths(root=root)


# --- parse_summary ---

def test_parse_summary_extracts_yaml_block():
    output = (
        "Some preamble the model shouldn't write, but let's be lenient.\n"
        "SUMMARY:\n"
        "```yaml\n"
        "current_summary: doing the thing\n"
        "open_issues:\n"
        "  - fix x\n"
        "```\n"
    )
    parsed = parse_summary(output)
    assert parsed is not None
    assert parsed["current_summary"] == "doing the thing"
    assert parsed["open_issues"] == ["fix x"]


def test_parse_summary_returns_none_without_marker():
    assert parse_summary("# [dry-run] summarizer\n\nNo model was called.\n") is None


def test_parse_summary_tolerates_markdown_decoration():
    output = "**SUMMARY:**\n```yaml\ncurrent_summary: ok\n```\n"
    parsed = parse_summary(output)
    assert parsed is not None
    assert parsed["current_summary"] == "ok"


# --- merge_memory ---

def test_merge_replaces_current_summary_and_open_issues():
    existing = MemoryRecord(current_summary="old summary", open_issues=["old issue"])
    merged = merge_memory(existing, {
        "current_summary": "new summary",
        "open_issues": ["new issue"],
    })
    assert merged.current_summary == "new summary"
    assert merged.open_issues == ["new issue"]


def test_merge_appends_decisions_without_duplicating():
    existing = MemoryRecord(decisions=[Decision(decision="kept the old API")])
    merged = merge_memory(existing, {
        "decisions": ["kept the old API", "added a new endpoint"],
    })
    texts = [d.decision for d in merged.decisions]
    assert texts == ["kept the old API", "added a new endpoint"]


def test_merge_appends_resolved_issues_without_duplicating():
    existing = MemoryRecord(resolved_issues=["fixed the path bug"])
    merged = merge_memory(existing, {
        "resolved_issues": ["fixed the path bug", "fixed the flaky test"],
    })
    assert merged.resolved_issues == ["fixed the path bug", "fixed the flaky test"]


def test_merge_ignores_missing_fields():
    existing = MemoryRecord(current_summary="stays the same", open_issues=["stays too"])
    merged = merge_memory(existing, {"decisions": ["a new decision"]})
    assert merged.current_summary == "stays the same"
    assert merged.open_issues == ["stays too"]
    assert merged.decisions[0].decision == "a new decision"


# --- summarize() end to end ---

class _SummaryProvider(DryRunProvider):
    name = "scripted_summarizer"

    def __init__(self, summary_yaml: str):
        self.summary_yaml = summary_yaml

    def run(self, request: ProviderRequest) -> ProviderResult:
        return ProviderResult(
            ok=True,
            output=f"SUMMARY:\n```yaml\n{self.summary_yaml}\n```\n",
            provider=self.name,
        )


def test_summarize_persists_memory(paths: AiPaths):
    wi = create_workitem(paths, "summarize me")
    approve_goal(paths, wi.workitem_id)
    provider = _SummaryProvider("current_summary: making progress\nopen_issues:\n  - one thing left")

    result = summarize(paths, wi, provider, "finish", [], execution_cwd=None)

    assert result is not None
    assert result.current_summary == "making progress"
    reloaded = load_memory(paths, wi.workitem_id)
    assert reloaded.current_summary == "making progress"
    assert (wi.directory / "context" / "current_summary.md").is_file()


def test_summarize_no_op_when_provider_makes_no_proposal(paths: AiPaths):
    wi = create_workitem(paths, "no summary")
    approve_goal(paths, wi.workitem_id)

    result = summarize(paths, wi, DryRunProvider(), "finish", [], execution_cwd=None)

    assert result is None
    assert not (wi.directory / "memory.yml").is_file()


def test_summarize_no_op_when_provider_fails(paths: AiPaths):
    wi = create_workitem(paths, "provider fails")
    approve_goal(paths, wi.workitem_id)

    class FailingProvider(DryRunProvider):
        def run(self, request):
            return ProviderResult(ok=False, output="", provider="failing", error="boom")

    result = summarize(paths, wi, FailingProvider(), "finish", [], execution_cwd=None)
    assert result is None
