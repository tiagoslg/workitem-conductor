"""Workitem lifecycle: id generation, creation, state I/O and the active pointer.

A workitem lives in ``.ai/workitems/<id>/`` and owns its goal contract, state and
(later) provider outputs/reviews. Ids follow the reference convention
``YYYY-MM-DD_<slug>`` so they sort chronologically and read clearly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..paths import AiPaths
from .models import GoalContract, Scope, WorkitemState

_SLUG_MAX_WORDS = 8


def slugify(text: str) -> str:
    """Turn free text into a kebab-case slug (lowercase, ascii, max few words)."""
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if not text:
        return "workitem"
    words = text.split("-")
    return "-".join(words[:_SLUG_MAX_WORDS])


def generate_id(goal: str, workitems_dir: Path, today: date | None = None) -> str:
    """Build a unique ``YYYY-MM-DD_<slug>`` id, suffixing ``-2``, ``-3`` on collision."""
    stamp = (today or date.today()).isoformat()
    base = f"{stamp}_{slugify(goal)}"
    candidate = base
    suffix = 2
    while (workitems_dir / candidate).exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def title_from_goal(goal: str) -> str:
    """A short human title derived from the goal's first line."""
    first = goal.strip().splitlines()[0].strip() if goal.strip() else "Untitled"
    return first[:80]


@dataclass(frozen=True)
class Workitem:
    """A located workitem: its id, directory, goal and state."""

    workitem_id: str
    directory: Path
    goal: GoalContract
    state: WorkitemState


def _goal_path(directory: Path) -> Path:
    return directory / "goal.yml"


def _state_path(directory: Path) -> Path:
    return directory / "state.yml"


def create_workitem(paths: AiPaths, goal: str, flow: str = "simple-change") -> Workitem:
    """Create a new workitem directory with goal + state, and mark it active."""
    paths.workitems_dir.mkdir(parents=True, exist_ok=True)
    workitem_id = generate_id(goal, paths.workitems_dir)
    directory = paths.workitem_dir(workitem_id)
    (directory / "outputs").mkdir(parents=True, exist_ok=True)
    (directory / "reviews").mkdir(parents=True, exist_ok=True)

    contract = GoalContract(goal=goal.strip(), scope=Scope())
    state = WorkitemState(
        workitem_id=workitem_id,
        title=title_from_goal(goal),
        flow=flow,
        artifacts={
            "goal": "goal.yml",
            "plan": None,
            "implementation": None,
            "review": None,
            "final_report": None,
        },
    )
    state.record("workitem created from goal; awaiting goal approval")

    _goal_path(directory).write_text(contract.to_yaml(), encoding="utf-8")
    _state_path(directory).write_text(state.to_yaml(), encoding="utf-8")
    set_active_id(paths, workitem_id)

    return Workitem(workitem_id=workitem_id, directory=directory, goal=contract, state=state)


def load_workitem(paths: AiPaths, workitem_id: str) -> Workitem:
    """Load goal + state for ``workitem_id`` from disk."""
    directory = paths.workitem_dir(workitem_id)
    if not directory.is_dir():
        raise FileNotFoundError(f"Workitem not found: {workitem_id}")
    goal = GoalContract.from_yaml(_goal_path(directory).read_text(encoding="utf-8"))
    state = WorkitemState.from_yaml(_state_path(directory).read_text(encoding="utf-8"))
    return Workitem(workitem_id=workitem_id, directory=directory, goal=goal, state=state)


def save_state(paths: AiPaths, state: WorkitemState) -> None:
    """Persist ``state`` back to its workitem's ``state.yml``."""
    directory = paths.workitem_dir(state.workitem_id)
    _state_path(directory).write_text(state.to_yaml(), encoding="utf-8")


def save_goal(paths: AiPaths, workitem_id: str, goal: GoalContract) -> None:
    """Persist ``goal`` back to its workitem's ``goal.yml``."""
    directory = paths.workitem_dir(workitem_id)
    _goal_path(directory).write_text(goal.to_yaml(), encoding="utf-8")


def approve_goal(paths: AiPaths, workitem_id: str) -> Workitem:
    """Mark the goal approved and advance the state so the two stay in sync.

    Sets ``approved: true`` in ``goal.yml`` and moves ``state.yml`` from
    ``draft``/``approve_goal`` to ``ready``/``execute``. Safe to call when the
    goal was already approved by a manual edit — it reconciles a state that
    lagged behind.
    """
    wi = load_workitem(paths, workitem_id)
    if not wi.goal.approved:
        wi.goal.approved = True
        save_goal(paths, workitem_id, wi.goal)
    wi.state.status = "ready"
    wi.state.next_action = "execute"
    wi.state.record("goal approved; ready to execute")
    save_state(paths, wi.state)
    return load_workitem(paths, workitem_id)


def list_workitems(paths: AiPaths) -> list[str]:
    """All workitem ids, sorted (chronological thanks to the date prefix)."""
    if not paths.workitems_dir.is_dir():
        return []
    return sorted(
        p.name
        for p in paths.workitems_dir.iterdir()
        if p.is_dir() and (p / "state.yml").exists()
    )


def set_active_id(paths: AiPaths, workitem_id: str) -> None:
    paths.active_pointer.write_text(workitem_id + "\n", encoding="utf-8")


def get_active_id(paths: AiPaths) -> str | None:
    """Return the active workitem id, or ``None`` if unset/missing on disk."""
    if not paths.active_pointer.exists():
        return None
    value = paths.active_pointer.read_text(encoding="utf-8").strip()
    if not value:
        return None
    if not paths.workitem_dir(value).is_dir():
        return None
    return value
