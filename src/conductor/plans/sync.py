"""``conductor plans sync`` — write a normalized opencode.db snapshot per plan.

The snapshot is the source of truth for execution evidence from this point
on; ``plans list --with-execution`` reads it, never ``opencode.db`` directly.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from ..home import data_home
from .execution import ExecutionTotals, PlanExecutionSnapshot
from .models import Plan
from .opencode_db import default_db_path, find_marked_session_ids, session_snapshots


def snapshot_path(plan_id: str) -> Path:
    return data_home() / "conductor" / "plans" / plan_id / "opencode-sessions.json"


def sync_plan(plan: Plan, db_path: Path | None = None) -> PlanExecutionSnapshot:
    resolved_db = db_path or default_db_path()
    marked = find_marked_session_ids(resolved_db, plan.id)
    sessions = session_snapshots(resolved_db, marked)
    snapshot = PlanExecutionSnapshot(
        plan_id=plan.id,
        synced_at=_dt.date.today().isoformat(),
        opencode_db_path=str(resolved_db),
        matched_via="plan_id_marker" if sessions else "none",
        sessions=sessions,
        totals=ExecutionTotals.from_sessions(sessions),
    )
    path = snapshot_path(plan.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot.model_dump(), indent=2), encoding="utf-8")
    return snapshot


def read_snapshot(plan_id: str) -> PlanExecutionSnapshot | None:
    path = snapshot_path(plan_id)
    if not path.exists():
        return None
    return PlanExecutionSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
