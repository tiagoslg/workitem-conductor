"""Tests for MemoryRecord I/O (memory.yml + context/current_summary.md)."""

from pathlib import Path

import pytest

from conductor.paths import AiPaths
from conductor.scaffold import scaffold_ai
from conductor.workitems.manager import create_workitem, load_memory, save_memory
from conductor.workitems.models import Decision, MemoryRecord, ValidationStatus


@pytest.fixture
def paths(tmp_path: Path) -> AiPaths:
    root = tmp_path / ".ai"
    scaffold_ai(root)
    return AiPaths(root=root)


def test_load_memory_returns_empty_record_when_absent(paths: AiPaths):
    wi = create_workitem(paths, "no memory yet")
    memory = load_memory(paths, wi.workitem_id)
    assert memory == MemoryRecord()


def test_save_and_load_memory_round_trips(paths: AiPaths):
    wi = create_workitem(paths, "round trip")
    memory = MemoryRecord(
        current_summary="halfway through the migration",
        decisions=[Decision(decision="kept old field name for compat")],
        open_issues=["fix the flaky test"],
        resolved_issues=["path bug fixed"],
        validation_status=ValidationStatus(last_tests=["pytest tests/"], failing=[]),
    )
    save_memory(paths, wi.workitem_id, memory)

    reloaded = load_memory(paths, wi.workitem_id)
    assert reloaded.current_summary == "halfway through the migration"
    assert reloaded.decisions[0].decision == "kept old field name for compat"
    assert reloaded.open_issues == ["fix the flaky test"]
    assert reloaded.resolved_issues == ["path bug fixed"]
    assert reloaded.validation_status.last_tests == ["pytest tests/"]


def test_save_memory_writes_current_summary_md(paths: AiPaths):
    wi = create_workitem(paths, "summary file")
    save_memory(paths, wi.workitem_id, MemoryRecord(current_summary="  the current state  "))

    summary_path = wi.directory / "context" / "current_summary.md"
    assert summary_path.is_file()
    assert summary_path.read_text(encoding="utf-8") == "the current state\n"
