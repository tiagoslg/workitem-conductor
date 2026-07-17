"""Shared helpers for parsing YAML that an LLM wrote inside a fenced block.

Extracted from ``refine.py`` (the ``CONTRACT:`` gate) so ``summarize.py`` (the
``SUMMARY:`` gate) doesn't duplicate the same hardening: models routinely
write YAML-hostile list items (bare colons, TypeScript-like union syntax)
that parse silently into the wrong shape rather than raising.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import yaml

FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


def preprocess_yaml(text: str) -> str | None:
    """Quote list-item values that contain YAML flow indicators or bare colons.

    Handles the two most common model mistakes in YAML written free-form:
    - TypeScript-like syntax: ``{ type: 'x'|'y' }`` (flow indicators).
    - Bare colon-space mid-sentence: ``at minimum: broken refs`` (YAML would
      parse the list item as a nested mapping instead of a plain string).

    Only touches single-line list items (``- value``); leaves mapping keys,
    already-quoted values, and multi-line block scalars alone.
    Returns the preprocessed string if it then parses cleanly, else None.
    """
    lines = []
    for line in text.splitlines():
        m = re.match(r'^(\s*-\s+)(.+)$', line)
        if m:
            value = m.group(2)
            needs_quoting = (
                re.search(r'[{|}]', value)    # flow indicators
                or re.search(r'\S:\s', value)  # colon-space mid-sentence
                or re.search(r'\S:$', value)   # trailing colon → YAML mapping key
            )
            if needs_quoting and not (value.startswith('"') or value.startswith("'")):
                line = m.group(1) + '"' + value.replace('"', '\\"') + '"'
        lines.append(line)
    cleaned = "\n".join(lines)
    try:
        yaml.safe_load(cleaned)
        return cleaned
    except yaml.YAMLError:
        return None


def coerce_str_list(v: object) -> object:
    """Coerce non-string list items to strings, for use in pydantic field validators.

    YAML parses a list item that ends with ``:`` as a mapping key, turning
    ``- Verify the endpoint:`` into ``{"Verify the endpoint": ...}``.
    Round-trip the mapping through ``yaml.dump`` so the item is still
    readable rather than raising a ``ValidationError`` that crashes the
    caller (e.g. ``conductor status``).
    """
    if not isinstance(v, list):
        return v
    result: list[str] = []
    for item in v:
        if isinstance(item, str):
            result.append(item)
        elif item is not None:
            result.append(
                yaml.dump(item, default_flow_style=False, allow_unicode=True).strip()
            )
    return result


def extract_fenced_yaml(
    after: str, valid: Callable[[dict], bool] = lambda d: True
) -> dict | None:
    """Parse the fenced (or unfenced) YAML block in ``after`` into a dict.

    Tries a raw ``yaml.safe_load`` first (accepting the result only if
    ``valid()`` approves — e.g. rejecting a silent bare-colon misparse where
    list items became dicts instead of strings). Falls back to
    ``preprocess_yaml`` and retries on failure. Returns ``None`` if nothing
    parses to a valid dict.
    """
    fence = FENCE_RE.search(after)
    if fence:
        raw = fence.group(1)
    else:
        # The marker's YAML may have been written as if already inside a code
        # fence; strip a stray closing ``` so yaml.safe_load sees clean YAML.
        raw = re.split(r'(?m)^```\s*$', after)[0]

    try:
        data = yaml.safe_load(raw)
        if isinstance(data, dict) and valid(data):
            return data
    except yaml.YAMLError:
        pass

    preprocessed = preprocess_yaml(raw)
    if preprocessed is None:
        return None
    try:
        data = yaml.safe_load(preprocessed)
        if isinstance(data, dict):
            return data
    except yaml.YAMLError:
        pass
    return None
