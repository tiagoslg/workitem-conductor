import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from conductor.dashboard.scan import collect
from conductor.dashboard.server import build_handler
from conductor.workitems.manager import approve_goal, create_workitem
from conductor.paths import AiPaths
from conductor.scaffold import scaffold_ai
from conductor.workspaces import WorkspaceRegistry, add_project


def _make_project(root: Path, goal: str, approve: bool = False) -> None:
    scaffold_ai(root / ".ai")
    paths = AiPaths(root=root / ".ai")
    wi = create_workitem(paths, goal)
    if approve:
        approve_goal(paths, wi.workitem_id)


@pytest.fixture
def registry(tmp_path: Path) -> WorkspaceRegistry:
    a, b, missing = tmp_path / "a", tmp_path / "b", tmp_path / "missing"
    _make_project(a, "first goal", approve=True)
    _make_project(b, "second goal")
    missing.mkdir()  # registered but has no .ai/
    reg = WorkspaceRegistry()
    for p in (a, b, missing):
        add_project(reg, p)
    return reg


def test_collect_reports_projects_and_workitems(registry: WorkspaceRegistry):
    data = collect(registry)
    assert data["workspace"] == "all"
    by_name = {p["name"]: p for p in data["projects"]}

    assert by_name["a"]["workitems"][0]["title"] == "first goal"
    assert by_name["a"]["workitems"][0]["active"] is True
    assert by_name["a"]["workitems"][0]["approved"] is True
    assert by_name["a"]["workitems"][0]["status"] == "ready"

    assert by_name["b"]["workitems"][0]["status"] == "draft"

    # a registered path with no .ai/ yields an error entry, not a crash
    assert "error" in by_name["missing"]
    assert by_name["missing"]["workitems"] == []


def test_server_serves_page_and_state(registry: WorkspaceRegistry):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(registry, None))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    base = f"http://127.0.0.1:{port}"
    try:
        html = urllib.request.urlopen(base + "/", timeout=5).read().decode("utf-8")
        assert "conductor dashboard" in html

        payload = urllib.request.urlopen(base + "/api/state", timeout=5).read()
        state = json.loads(payload)
        assert len(state["projects"]) == 3

        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(base + "/nope", timeout=5)
        assert exc.value.code == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
