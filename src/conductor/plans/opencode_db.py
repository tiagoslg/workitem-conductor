"""Read-only access to OpenCode's own ``opencode.db`` (SQLite).

The conductor never writes here. This module only finds the sessions that
executed a given plan (via the ``PLAN_ID: <id>`` marker OpenCode's
``implement-plan.md`` command injects) and their direct subagent children.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .execution import SessionSnapshot

_MARKER_RE = re.compile(r"^PLAN_ID:\s*(\S+)", re.MULTILINE)


class OpenCodeDbNotFound(Exception):
    def __init__(self, path: Path):
        self.path = path
        super().__init__(f"opencode.db not found at {path}")


def default_db_path() -> Path:
    override = os.environ.get("CONDUCTOR_OPENCODE_DB")
    if override:
        return Path(override)
    return Path.home() / ".local" / "share" / "opencode" / "opencode.db"


@contextmanager
def _connect(db_path: Path):
    if not db_path.exists():
        raise OpenCodeDbNotFound(db_path)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        yield con
    finally:
        con.close()


def find_marked_session_ids(db_path: Path, plan_id: str) -> set[str]:
    """Session ids whose text carries an exact ``PLAN_ID: <plan_id>`` marker."""
    with _connect(db_path) as con:
        return _find_marked_session_ids(con, plan_id)


def _find_marked_session_ids(con: sqlite3.Connection, plan_id: str) -> set[str]:
    cur = con.cursor()
    cur.execute("SELECT session_id, data FROM part WHERE data LIKE '%PLAN_ID:%'")
    matched: set[str] = set()
    for session_id, raw in cur.fetchall():
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        text = data.get("text")
        if not isinstance(text, str):
            continue
        m = _MARKER_RE.search(text)
        if m and m.group(1) == plan_id:
            matched.add(session_id)
    return matched


def session_snapshots(db_path: Path, session_ids: set[str]) -> list[SessionSnapshot]:
    """Snapshots for the given sessions plus their direct subagent children."""
    with _connect(db_path) as con:
        return _session_snapshots(con, session_ids)


def _session_snapshots(con: sqlite3.Connection, session_ids: set[str]) -> list[SessionSnapshot]:
    if not session_ids:
        return []
    cur = con.cursor()
    placeholders = ",".join("?" for _ in session_ids)
    ids = list(session_ids)
    cur.execute(
        f"""
        SELECT id, parent_id, agent, model, directory, cost,
               tokens_input, tokens_output, tokens_reasoning,
               tokens_cache_read, tokens_cache_write,
               time_created, time_updated
        FROM session
        WHERE id IN ({placeholders}) OR parent_id IN ({placeholders})
        """,
        ids + ids,
    )
    rows = cur.fetchall()
    return [_row_to_snapshot(row) for row in rows]


def _row_to_snapshot(row: tuple) -> SessionSnapshot:
    (
        id_,
        parent_id,
        agent,
        model_raw,
        directory,
        cost,
        tokens_input,
        tokens_output,
        tokens_reasoning,
        tokens_cache_read,
        tokens_cache_write,
        time_created,
        time_updated,
    ) = row
    model = None
    if model_raw:
        try:
            parsed = json.loads(model_raw)
            provider = parsed.get("providerID")
            model_id = parsed.get("id")
            model = f"{provider}/{model_id}" if provider and model_id else model_id or provider
        except (TypeError, ValueError):
            model = None
    return SessionSnapshot(
        id=id_,
        agent=agent,
        model=model,
        parent_id=parent_id,
        directory=directory,
        cost=cost or 0.0,
        tokens_input=tokens_input or 0,
        tokens_output=tokens_output or 0,
        tokens_reasoning=tokens_reasoning or 0,
        tokens_cache_read=tokens_cache_read or 0,
        tokens_cache_write=tokens_cache_write or 0,
        time_created=time_created,
        time_updated=time_updated,
    )
