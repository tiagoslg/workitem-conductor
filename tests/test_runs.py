"""Tests for per-run manifests (runs/<id>/run.yml + metrics.yml)."""

from pathlib import Path

import pytest

from conductor.core.engine import Engine
from conductor.core.runs import list_run_ids, load_metrics, load_run, next_run_id
from conductor.flows.loader import load_flow
from conductor.paths import AiPaths
from conductor.providers.dryrun import DryRunProvider
from conductor.scaffold import scaffold_ai
from conductor.workitems.manager import approve_goal, create_workitem, reopen_workitem


@pytest.fixture
def paths(tmp_path: Path) -> AiPaths:
    root = tmp_path / ".ai"
    scaffold_ai(root)
    return AiPaths(root=root)


def test_next_run_id_sequences(tmp_path: Path):
    workitem_dir = tmp_path / "wi-001"
    assert next_run_id(workitem_dir) == "run-001"

    (workitem_dir / "runs" / "run-001").mkdir(parents=True)
    assert next_run_id(workitem_dir) == "run-002"

    (workitem_dir / "runs" / "run-007").mkdir(parents=True)
    assert next_run_id(workitem_dir) == "run-008"


def test_engine_run_writes_run_manifest(paths: AiPaths):
    wi = create_workitem(paths, "do the thing")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")
    engine = Engine(paths, flow, provider_for=lambda role: DryRunProvider())

    outcome = engine.run(wi.workitem_id)
    assert outcome.completed is True

    run_ids = list_run_ids(wi.directory)
    assert run_ids == ["run-001"]

    run = load_run(wi.directory, "run-001")
    assert run.run_id == "run-001"
    assert run.workitem_id == wi.workitem_id
    assert run.status == "completed"
    assert run.flow == "simple-change"
    assert run.reopen_number == 0
    assert [s.role for s in run.steps] == ["planner", "implementer", "reviewer", "validator"]
    assert all(s.ok for s in run.steps)
    assert all(s.duration_sec >= 0 for s in run.steps)
    assert all(s.prompt_chars > 0 for s in run.steps)
    assert all(s.output_chars > 0 for s in run.steps)

    # run.yml must reference the actual outputs/ artifacts for this run —
    # it's a manifest, not a copy, but has to be auditable by path
    assert [s.index for s in run.steps] == [0, 1, 2, 3]
    for step in run.steps:
        assert (wi.directory / step.prompt_path).is_file()
        assert (wi.directory / step.output_path).is_file()
        assert step.prompt_path.startswith("outputs/")
        assert step.output_path.startswith("outputs/")

    metrics = load_metrics(wi.directory, "run-001")
    assert metrics is not None
    assert metrics.context["total_prompt_chars"] > 0
    assert metrics.context["max_step_prompt_chars"] > 0
    assert metrics.loop == {"fix_iterations": 0, "reopen_number": 0}
    assert metrics.providers["planner"] == "dry_run"
    # execution_cwd here is a bare tmp dir, not a git repo
    assert metrics.git is None


def test_reopen_then_execute_creates_second_run(paths: AiPaths):
    wi = create_workitem(paths, "iterate twice")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")
    engine = Engine(paths, flow, provider_for=lambda role: DryRunProvider())

    engine.run(wi.workitem_id)
    reopen_workitem(paths, wi.workitem_id, "second pass")
    engine.run(wi.workitem_id)

    run_ids = list_run_ids(wi.directory)
    assert run_ids == ["run-001", "run-002"]

    second = load_run(wi.directory, "run-002")
    assert second.reopen_number == 1


def test_run_manifest_written_on_provider_failure(paths: AiPaths):
    from conductor.providers.base import ProviderResult

    wi = create_workitem(paths, "will fail")
    approve_goal(paths, wi.workitem_id)
    flow = load_flow(paths, "simple-change")

    class FailingProvider(DryRunProvider):
        name = "failing"

        def run(self, request):
            return ProviderResult(ok=False, output="", provider=self.name, error="boom")

    engine = Engine(paths, flow, provider_for=lambda role: FailingProvider())
    outcome = engine.run(wi.workitem_id)
    assert outcome.completed is False

    run = load_run(wi.directory, "run-001")
    assert run.status == "blocked"
    assert run.stopped_reason is not None
    assert len(run.steps) == 1
    assert run.steps[0].ok is False
