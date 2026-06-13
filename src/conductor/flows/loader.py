"""Loading flow definitions from the repository's ``.ai/flows/`` directory."""

from __future__ import annotations

import yaml

from ..paths import AiPaths
from .models import Flow


class FlowNotFound(Exception):
    def __init__(self, name: str, path: str) -> None:
        super().__init__(f"Flow '{name}' not found at {path}")
        self.name = name


def load_flow(paths: AiPaths, name: str) -> Flow:
    """Load and validate the flow named ``name`` (without the ``.yml`` suffix)."""
    flow_path = paths.flows_dir / f"{name}.yml"
    if not flow_path.is_file():
        raise FlowNotFound(name, str(flow_path))
    data = yaml.safe_load(flow_path.read_text(encoding="utf-8")) or {}
    data.setdefault("name", name)
    return Flow.model_validate(data)
