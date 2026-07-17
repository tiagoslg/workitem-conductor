"""Tests for the Strategy model, loader, and rule-based selector (M7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from conductor.config.models import ContextConfig
from conductor.paths import AiPaths
from conductor.scaffold import scaffold_ai
from conductor.strategies.loader import StrategyNotFound, load_strategy, strategy_content_hash
from conductor.strategies.selector import select_strategy
from conductor.workitems.manager import create_workitem


@pytest.fixture
def paths(tmp_path: Path) -> AiPaths:
    root = tmp_path / ".ai"
    scaffold_ai(root)
    return AiPaths(root=root)


def test_scaffolded_builtin_strategies_load(paths: AiPaths):
    for name in ("simple-change", "bugfix", "context-heavy-change"):
        strategy = load_strategy(paths, name)
        assert strategy.name == name
        assert strategy.flow == "simple-change"

    assert load_strategy(paths, "phased-documentation").flow == "phased-change"
    assert load_strategy(paths, "bugfix").max_fix_iterations == 2
    heavy = load_strategy(paths, "context-heavy-change")
    assert heavy.context.include_raw_outputs is True
    assert heavy.context.max_prompt_chars == 96_000


def test_load_strategy_missing_raises(paths: AiPaths):
    with pytest.raises(StrategyNotFound):
        load_strategy(paths, "no-such-strategy")


def test_strategy_round_trips_roles_and_context(tmp_path: Path):
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    (strategies_dir / "custom.yml").write_text(
        "flow: simple-change\n"
        "roles:\n"
        "  implementer: { provider: some_other }\n"
        "context:\n"
        "  max_prompt_chars: 12345\n"
        "max_fix_iterations: 1\n",
        encoding="utf-8",
    )
    paths = AiPaths(root=tmp_path)
    strategy = load_strategy(paths, "custom")
    assert strategy.name == "custom"
    assert strategy.roles["implementer"].provider == "some_other"
    assert isinstance(strategy.context, ContextConfig)
    assert strategy.context.max_prompt_chars == 12345
    assert strategy.max_fix_iterations == 1


def test_strategy_content_hash_changes_with_content(tmp_path: Path):
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    (strategies_dir / "custom.yml").write_text("flow: simple-change\n", encoding="utf-8")
    paths = AiPaths(root=tmp_path)
    h1 = strategy_content_hash(paths, "custom")
    assert len(h1) == 8

    (strategies_dir / "custom.yml").write_text("flow: simple-change\ndescription: changed\n", encoding="utf-8")
    h2 = strategy_content_hash(paths, "custom")
    assert h1 != h2


def test_selector_picks_phased_documentation_for_docs_criteria(paths: AiPaths):
    wi = create_workitem(paths, "improve the API")
    wi.goal.acceptance_criteria = ["Update the docs to reflect the new endpoint"]
    assert select_strategy(wi.goal, wi.state) == "phased-documentation"


def test_selector_defaults_to_simple_change(paths: AiPaths):
    wi = create_workitem(paths, "fix a bug")
    wi.goal.acceptance_criteria = ["The endpoint returns 200"]
    assert select_strategy(wi.goal, wi.state) == "simple-change"


def test_selector_defaults_to_simple_change_with_no_criteria(paths: AiPaths):
    wi = create_workitem(paths, "anything")
    assert select_strategy(wi.goal, wi.state) == "simple-change"
