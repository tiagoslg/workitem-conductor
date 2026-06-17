"""Unit tests for context assembly (no engine, no providers)."""

from pathlib import Path

import pytest

from conductor.core.context import _MAX_OUTPUT_CHARS, _prior_outputs, build_context
from conductor.paths import AiPaths
from conductor.scaffold import scaffold_ai
from conductor.workitems.manager import create_workitem, load_workitem


@pytest.fixture
def paths(tmp_path: Path) -> AiPaths:
    root = tmp_path / ".ai"
    scaffold_ai(root)
    return AiPaths(root=root)


@pytest.fixture
def workitem(paths: AiPaths):
    return create_workitem(paths, "add a thing")


def _write_output(workitem, seq: int, role: str, text: str) -> None:
    out = workitem.directory / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{seq:02d}-{role}.output.md").write_text(text, encoding="utf-8")


# --- deduplication ---

def test_dedup_keeps_only_latest_per_role(workitem):
    _write_output(workitem, 1, "implementer", "first pass")
    _write_output(workitem, 3, "implementer", "fixed pass")

    results = _prior_outputs(workitem)
    names = [name for name, _ in results]
    assert names == ["03-implementer.output.md"]
    assert results[0][1] == "fixed pass"


def test_dedup_keeps_first_appearance_order(workitem):
    _write_output(workitem, 0, "planner", "plan")
    _write_output(workitem, 1, "implementer", "impl v1")
    _write_output(workitem, 2, "reviewer", "review")
    _write_output(workitem, 3, "implementer", "impl v2")  # second implementer round

    results = _prior_outputs(workitem)
    names = [name for name, _ in results]
    # implementer appeared first at 01, so it stays before reviewer
    assert names == ["00-planner.output.md", "03-implementer.output.md", "02-reviewer.output.md"]


# --- truncation ---

def test_truncation_at_max_chars(workitem):
    long_text = "x" * (_MAX_OUTPUT_CHARS + 200)
    _write_output(workitem, 0, "planner", long_text)

    results = _prior_outputs(workitem)
    _, text = results[0]
    assert len(text) < len(long_text)
    assert "truncated" in text
    assert "200 chars omitted" in text


def test_no_truncation_under_cap(workitem):
    short_text = "y" * (_MAX_OUTPUT_CHARS - 1)
    _write_output(workitem, 0, "planner", short_text)

    _, text = _prior_outputs(workitem)[0]
    assert text == short_text
    assert "truncated" not in text


# --- fix-loop header in build_context ---

def test_fix_loop_header_shown_when_fix_iterations_nonzero(paths, workitem):
    _write_output(workitem, 0, "planner", "the plan")
    workitem.state.fix_iterations = 2

    ctx = build_context(paths, workitem, "implementer")
    assert "Fix iteration 2" in ctx
    assert "earlier rounds omitted" in ctx


def test_no_fix_loop_header_on_first_run(paths, workitem):
    _write_output(workitem, 0, "planner", "the plan")
    # fix_iterations defaults to 0

    ctx = build_context(paths, workitem, "implementer")
    assert "Fix iteration" not in ctx
    assert "earlier rounds omitted" not in ctx


def test_no_prior_outputs_section_when_empty(paths, workitem):
    ctx = build_context(paths, workitem, "planner")
    assert "Prior step outputs" not in ctx


# --- reopen reason injection ---

def test_reopen_reason_injected_when_file_present(paths, workitem):
    (workitem.directory / "reopen.md").write_text(
        "reviewer flagged missing null-check", encoding="utf-8"
    )
    ctx = build_context(paths, workitem, "planner")
    assert "## Reopen reason" in ctx
    assert "reviewer flagged missing null-check" in ctx


def test_no_reopen_section_when_file_absent(paths, workitem):
    ctx = build_context(paths, workitem, "planner")
    assert "Reopen reason" not in ctx


def test_reopen_reason_appears_after_goal_contract(paths, workitem):
    (workitem.directory / "reopen.md").write_text("try again", encoding="utf-8")
    ctx = build_context(paths, workitem, "planner")
    goal_pos = ctx.index("## Goal contract")
    reopen_pos = ctx.index("## Reopen reason")
    assert goal_pos < reopen_pos
