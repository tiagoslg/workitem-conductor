"""Collect a read-only snapshot of workitems across registered projects.

The pure data layer behind the dashboard server: given a workspace registry, it
walks each project root, reuses the normal workitem read path
(``list_workitems`` / ``load_workitem``), and returns plain dicts ready to be
JSON-serialized. Any unreadable project becomes an ``error`` entry rather than
breaking the whole scan.
"""

from __future__ import annotations

from pathlib import Path

from ..paths import AiPaths
from ..workitems.manager import get_active_id, list_workitems, load_workitem
from ..workitems.models import utcnow_iso
from ..workspaces import WorkspaceRegistry, list_projects


def _workitem_view(state) -> dict:
    return {
        "id": state.workitem_id,
        "title": state.title,
        "flow": state.flow,
        "stage": state.stage,
        "status": state.status,
        "next_action": state.next_action,
        "open_issues": list(state.open_issues),
        "updated_at": state.updated_at,
    }


def _project_view(path: str) -> dict:
    root = Path(path)
    ai_root = root / ".ai"
    view: dict = {"path": path, "name": root.name, "workitems": []}
    if not (ai_root / "workitems").is_dir():
        view["error"] = "no .ai/ workitems here (run `conductor init`)"
        return view
    try:
        paths = AiPaths(root=ai_root)
        active = get_active_id(paths)
        for wid in list_workitems(paths):
            wi = load_workitem(paths, wid)
            item = _workitem_view(wi.state)
            item["approved"] = bool(wi.goal.approved)
            item["active"] = wid == active
            view["workitems"].append(item)
    except Exception as exc:  # never let one bad project break the scan
        view["error"] = f"failed to read: {exc}"
    return view


def collect(registry: WorkspaceRegistry, workspace: str | None = None) -> dict:
    """Return ``{generated, workspace, projects:[...]}`` for the dashboard."""
    projects = [_project_view(path) for path in list_projects(registry, workspace)]
    return {
        "generated": utcnow_iso(),
        "workspace": workspace or "all",
        "projects": projects,
    }
