"""Parsing the leading ``---\\n...\\n---`` frontmatter block of a plan file."""

from __future__ import annotations

import re

import yaml

from ..core.yaml_utils import preprocess_yaml

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def parse_frontmatter(text: str) -> dict | None:
    """Return the parsed frontmatter dict, or ``None`` if the file has none / it's malformed."""
    split = parse_frontmatter_and_body(text)
    return split[0] if split else None


def parse_frontmatter_and_body(text: str) -> tuple[dict, str] | None:
    """Return ``(frontmatter_dict, body_text)``, or ``None`` if unparseable."""
    m = _FRONTMATTER_RE.match(text)
    if m is None:
        return None
    raw = m.group(1)
    body = text[m.end():]
    data = None
    try:
        loaded = yaml.safe_load(raw)
        if isinstance(loaded, dict):
            data = loaded
    except yaml.YAMLError:
        pass
    if data is None:
        preprocessed = preprocess_yaml(raw)
        if preprocessed is None:
            return None
        try:
            loaded = yaml.safe_load(preprocessed)
            if isinstance(loaded, dict):
                data = loaded
        except yaml.YAMLError:
            return None
    if data is None:
        return None
    return data, body


def write_frontmatter_and_body(data: dict, body: str) -> str:
    """Re-serialize a frontmatter dict + body back into full file text."""
    dumped = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return f"---\n{dumped}---\n{body}"
