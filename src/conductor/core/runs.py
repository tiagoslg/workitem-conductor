"""Per-execution run records: what happened, on which provider, how long.

Each ``Engine.run()`` call is one "run". It doesn't relocate the existing
``outputs/NN-role.{prompt,output}.md`` artifacts — ``run.yml`` is a manifest
that references them and records what the flat output files don't capture
(provider used, duration, char counts, verdict). ``metrics.yml`` aggregates
those into the numbers a future comparison across strategies/providers needs.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

_RUN_DIRNAME_RE = re.compile(r"^run-(\d+)$")


class StepRecord(BaseModel):
    index: int
    role: str
    provider: str
    ok: bool
    duration_sec: float
    prompt_chars: int
    output_chars: int
    #: paths relative to the workitem directory, e.g. "outputs/00-planner.prompt.md"
    prompt_path: str
    output_path: str
    verdict: str | None = None
    looped_back: bool = False


class RunRecord(BaseModel):
    run_id: str
    workitem_id: str
    started_at: str
    finished_at: str
    status: str
    flow: str
    source: str = "execute"
    reopen_number: int = 0
    stopped_reason: str | None = None
    steps: list[StepRecord] = Field(default_factory=list)

    def to_yaml(self) -> str:
        return yaml.safe_dump(
            self.model_dump(), sort_keys=False, allow_unicode=True, default_flow_style=False
        )

    @classmethod
    def from_yaml(cls, text: str) -> "RunRecord":
        return cls.model_validate(yaml.safe_load(text) or {})


class MetricsRecord(BaseModel):
    context: dict = Field(default_factory=dict)
    git: dict | None = None
    loop: dict = Field(default_factory=dict)
    providers: dict[str, str] = Field(default_factory=dict)

    def to_yaml(self) -> str:
        return yaml.safe_dump(
            self.model_dump(), sort_keys=False, allow_unicode=True, default_flow_style=False
        )

    @classmethod
    def from_yaml(cls, text: str) -> "MetricsRecord":
        return cls.model_validate(yaml.safe_load(text) or {})


def next_run_id(workitem_dir: Path) -> str:
    """Return the next sequential ``run-NNN`` id under ``workitem_dir/runs/``."""
    runs_dir = workitem_dir / "runs"
    highest = 0
    if runs_dir.is_dir():
        for entry in runs_dir.iterdir():
            m = _RUN_DIRNAME_RE.match(entry.name)
            if m:
                highest = max(highest, int(m.group(1)))
    return f"run-{highest + 1:03d}"


def list_run_ids(workitem_dir: Path) -> list[str]:
    """All run ids for a workitem, sorted chronologically."""
    runs_dir = workitem_dir / "runs"
    if not runs_dir.is_dir():
        return []
    return sorted(
        p.name for p in runs_dir.iterdir()
        if p.is_dir() and _RUN_DIRNAME_RE.match(p.name)
    )


def load_run(workitem_dir: Path, run_id: str) -> RunRecord:
    return RunRecord.from_yaml((workitem_dir / "runs" / run_id / "run.yml").read_text(encoding="utf-8"))


def load_metrics(workitem_dir: Path, run_id: str) -> MetricsRecord | None:
    path = workitem_dir / "runs" / run_id / "metrics.yml"
    if not path.is_file():
        return None
    return MetricsRecord.from_yaml(path.read_text(encoding="utf-8"))


def write_run(workitem_dir: Path, run: RunRecord, metrics: MetricsRecord) -> Path:
    """Write ``run.yml`` and ``metrics.yml`` under ``workitem_dir/runs/<run_id>/``."""
    run_dir = workitem_dir / "runs" / run.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.yml").write_text(run.to_yaml(), encoding="utf-8")
    (run_dir / "metrics.yml").write_text(metrics.to_yaml(), encoding="utf-8")
    return run_dir
