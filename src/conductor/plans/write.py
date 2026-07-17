"""Updating a plan's frontmatter in place — the file stays the source of truth.

No separate database. ``conductor plans mark`` rewrites the frontmatter block
of the plan file itself; the body (prose) is left untouched.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from .frontmatter import parse_frontmatter_and_body, write_frontmatter_and_body


class PlanFileError(Exception):
    pass


def _load(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    parsed = parse_frontmatter_and_body(text)
    if parsed is None:
        raise PlanFileError(f"{path}: no valid frontmatter block")
    return parsed

def mark_ready(path: Path) -> None:
    """``draft`` -> ``ready``. Never used to write any other transition."""
    data, body = _load(path)
    data["status"] = "ready"
    path.write_text(write_frontmatter_and_body(data, body), encoding="utf-8")


def mark_done(path: Path, commit: str | None = None) -> None:
    """Append ``commit`` to ``commits`` (if given), set status done, stamp completed_at."""
    data, body = _load(path)
    if commit:
        commits = list(data.get("commits") or [])
        if commit not in commits:
            commits.append(commit)
        data["commits"] = commits
    data["status"] = "done"
    data["completed_at"] = _dt.date.today().isoformat()
    path.write_text(write_frontmatter_and_body(data, body), encoding="utf-8")
