from pathlib import Path

import pytest

from conductor.core.refine import (
    Refiner,
    _extract_contract_yaml,
    parse_refine_response,
)
from conductor.paths import AiPaths
from conductor.providers.base import Provider, ProviderResult
from conductor.providers.dryrun import DryRunProvider
from conductor.scaffold import scaffold_ai
from conductor.workitems.manager import create_workitem, load_workitem


@pytest.fixture
def paths(tmp_path: Path) -> AiPaths:
    root = tmp_path / ".ai"
    scaffold_ai(root)
    return AiPaths(root=root)


CONTRACT_RESPONSE = """\
Here is the contract.
CONTRACT:
```yaml
scope:
  include:
    - src/policy.py
  exclude: []
acceptance_criteria:
  - discovery returns active policies
constraints:
  - no public API changes
validation:
  - pytest tests/test_policy.py
stop_conditions:
  - requires a DB migration
```
"""

QUESTIONS_RESPONSE = """\
I need more detail first.
QUESTIONS:
1. Which module owns policy discovery?
2. Should the fix touch the API?
"""


# --- parser -----------------------------------------------------------------


def test_parse_questions_only():
    resp = parse_refine_response(QUESTIONS_RESPONSE)
    assert resp.kind == "questions"
    assert resp.questions == [
        "Which module owns policy discovery?",
        "Should the fix touch the API?",
    ]


def test_parse_contract_only():
    resp = parse_refine_response(CONTRACT_RESPONSE)
    assert resp.kind == "contract"
    assert resp.contract["scope"]["include"] == ["src/policy.py"]
    assert resp.contract["acceptance_criteria"] == ["discovery returns active policies"]


def test_parse_contract_wins_over_questions():
    text = QUESTIONS_RESPONSE + "\n" + CONTRACT_RESPONSE
    assert parse_refine_response(text).kind == "contract"


def test_parse_unknown_when_no_marker():
    assert parse_refine_response("just some prose, no marker").kind == "unknown"
    assert parse_refine_response("").kind == "unknown"


def test_parse_malformed_contract_is_unknown():
    text = "CONTRACT:\n```yaml\nscope: [unclosed\n```\n"
    assert parse_refine_response(text).kind == "unknown"


def test_parse_marker_case_insensitive_and_indented():
    text = "   contract:\n```\nacceptance_criteria: [done]\n```\n"
    resp = parse_refine_response(text)
    assert resp.kind == "contract"
    assert resp.contract["acceptance_criteria"] == ["done"]


def test_parse_marker_with_markdown_decoration():
    # smaller models wrap the marker in bold / headings
    bold = "**CONTRACT:**\n```yaml\nscope: {include: [a]}\n```\n"
    assert parse_refine_response(bold).kind == "contract"
    heading = "## QUESTIONS\n1. which module?\n"
    resp = parse_refine_response(heading)
    assert resp.kind == "questions"
    assert resp.questions == ["which module?"]


def test_infer_contract_from_bare_yaml_block():
    # no CONTRACT: marker, but a yaml block with contract keys
    text = (
        "Here is the contract for the change:\n"
        "```yaml\n"
        "scope:\n  include: [src/http.py]\n"
        "acceptance_criteria: [retries on 5xx]\n"
        "```\n"
    )
    resp = parse_refine_response(text)
    assert resp.kind == "contract"
    assert resp.contract["scope"]["include"] == ["src/http.py"]


def test_infer_questions_from_bare_numbered_list():
    # no QUESTIONS: marker (the real Qwen failure mode)
    text = (
        "I need a few more details before writing the contract:\n"
        "1. What config file format should be used?\n"
        "2. Which methods should retry?\n"
    )
    resp = parse_refine_response(text)
    assert resp.kind == "questions"
    assert resp.questions == [
        "What config file format should be used?",
        "Which methods should retry?",
    ]


def test_prose_without_list_is_not_mistaken_for_questions():
    # a question mark in prose must not trigger the questions path
    assert parse_refine_response("Is this clear enough? I think so.").kind == "unknown"


def test_unrelated_code_block_is_not_a_contract():
    text = "```python\nprint('hello')\n```\n"
    assert parse_refine_response(text).kind == "unknown"


# --- driver -----------------------------------------------------------------


class ScriptedRefiner(Provider):
    """Emits a fixed sequence of responses; last one repeats."""

    name = "scripted"

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls = 0

    def run(self, request) -> ProviderResult:
        text = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return ProviderResult(ok=True, output=text, provider=self.name)


def test_refine_asks_then_writes_contract(paths: AiPaths):
    wi = create_workitem(paths, "fix the policy discovery bug")
    provider = ScriptedRefiner([QUESTIONS_RESPONSE, QUESTIONS_RESPONSE, CONTRACT_RESPONSE])

    asked: list[list[str]] = []

    def ask(questions: list[str]) -> list[str]:
        asked.append(list(questions))
        return [f"answer-{i}" for i in range(len(questions))]

    refiner = Refiner(paths, provider_for=lambda role: provider)
    outcome = refiner.run(wi.workitem_id, ask=ask)

    assert outcome.updated is True
    assert outcome.rounds == 3
    assert len(asked) == 2  # two question rounds before the contract

    reloaded = load_workitem(paths, wi.workitem_id)
    # proposed fields applied
    assert reloaded.goal.scope.include == ["src/policy.py"]
    assert reloaded.goal.acceptance_criteria == ["discovery returns active policies"]
    assert reloaded.goal.constraints == ["no public API changes"]
    # goal text and approval preserved
    assert reloaded.goal.goal == "fix the policy discovery bug"
    assert reloaded.goal.approved is False

    refine_md = wi.directory / "refine.md"
    assert refine_md.is_file()
    assert "question rounds: 2" in refine_md.read_text()
    assert reloaded.state.artifacts["refine"] == "refine.md"
    assert any("goal refined" in h.summary for h in reloaded.state.history)


def test_refine_writes_contract_without_questions(paths: AiPaths):
    wi = create_workitem(paths, "tidy the logging")
    provider = ScriptedRefiner([CONTRACT_RESPONSE])

    asked: list[list[str]] = []
    refiner = Refiner(paths, provider_for=lambda role: provider)
    outcome = refiner.run(wi.workitem_id, ask=lambda q: asked.append(q) or [])

    assert outcome.updated is True
    assert outcome.rounds == 1
    assert asked == []  # never asked
    assert "question rounds: 0" in (wi.directory / "refine.md").read_text()


def test_refine_stops_at_max_rounds(paths: AiPaths):
    wi = create_workitem(paths, "endless questions")
    provider = ScriptedRefiner([QUESTIONS_RESPONSE])  # always asks
    before = load_workitem(paths, wi.workitem_id).goal.to_yaml()

    refiner = Refiner(paths, provider_for=lambda role: provider, max_rounds=2)
    outcome = refiner.run(wi.workitem_id, ask=lambda q: ["x" for _ in q])

    assert outcome.updated is False
    assert "max question rounds" in outcome.stopped_reason
    # goal untouched, no refine.md written
    assert load_workitem(paths, wi.workitem_id).goal.to_yaml() == before
    assert not (wi.directory / "refine.md").exists()


def test_refine_dry_run_is_a_clean_noop(paths: AiPaths):
    wi = create_workitem(paths, "dry run refine")
    refiner = Refiner(paths, provider_for=lambda role: DryRunProvider())
    outcome = refiner.run(wi.workitem_id, ask=lambda q: [])

    assert outcome.updated is False
    assert outcome.rounds == 1
    assert "no QUESTIONS:/CONTRACT:" in outcome.stopped_reason


def test_refine_stops_on_provider_failure(paths: AiPaths):
    wi = create_workitem(paths, "failing refiner")

    class FailingRefiner(Provider):
        name = "failing"

        def run(self, request) -> ProviderResult:
            return ProviderResult(ok=False, output="", provider=self.name, error="boom")

    refiner = Refiner(paths, provider_for=lambda role: FailingRefiner())
    outcome = refiner.run(wi.workitem_id, ask=lambda q: [])

    assert outcome.updated is False
    assert "provider failed" in outcome.stopped_reason
    assert "boom" in outcome.stopped_reason


def test_refine_persists_raw_round_outputs(paths: AiPaths):
    # every round's raw output is saved so a stop is diagnosable afterwards
    wi = create_workitem(paths, "raw capture")
    provider = ScriptedRefiner([QUESTIONS_RESPONSE, CONTRACT_RESPONSE])
    refiner = Refiner(paths, provider_for=lambda role: provider)
    refiner.run(wi.workitem_id, ask=lambda q: ["a"])

    raw_dir = wi.directory / "refine"
    assert (raw_dir / "01-raw.md").is_file()
    round1 = (raw_dir / "01-raw.md").read_text()
    assert "parsed: questions" in round1
    assert "QUESTIONS:" in round1  # the actual model output is preserved
    assert "parsed: contract" in (raw_dir / "02-raw.md").read_text()


def test_refine_recovers_from_markerless_questions(paths: AiPaths):
    # the real Qwen failure: a question list with no QUESTIONS: marker, then a contract
    markerless = "I need more detail:\n1. Which config format?\n2. Which methods retry?\n"
    wi = create_workitem(paths, "markerless")
    provider = ScriptedRefiner([markerless, CONTRACT_RESPONSE])
    asked: list[list[str]] = []
    refiner = Refiner(paths, provider_for=lambda role: provider)
    outcome = refiner.run(wi.workitem_id, ask=lambda q: asked.append(q) or ["x", "y"])

    assert outcome.updated is True
    assert asked == [["Which config format?", "Which methods retry?"]]


# --- _extract_contract_yaml preprocessing fallback ---

def _make_fenced(yaml_text: str) -> str:
    """Wrap yaml_text in a code fence so _extract_contract_yaml can find it."""
    return f"\n```yaml\n{yaml_text}\n```\n"


def test_yaml_clean_parses_without_fallback():
    raw = _make_fenced(
        "scope:\n  include: []\nacceptance_criteria:\n  - works correctly\n"
    )
    result = _extract_contract_yaml(raw)
    assert result is not None
    assert "scope" in result


def test_yaml_flow_indicators_recovered_by_preprocessor():
    # TypeScript-like syntax: { type: 'a'|'b' } would break yaml.safe_load
    raw = _make_fenced(
        "scope:\n  include: []\n"
        "acceptance_criteria:\n"
        "  - FlowIssue has fields { type: 'error'|'warning', message: string }\n"
    )
    result = _extract_contract_yaml(raw)
    assert result is not None
    assert "acceptance_criteria" in result


def test_yaml_colon_in_value_recovered_by_preprocessor():
    # Bare `: ` mid-sentence makes YAML parse the list item as a mapping
    raw = _make_fenced(
        "scope:\n  include: []\n"
        "acceptance_criteria:\n"
        "  - validateFlow returns FlowIssue[] covering at minimum: broken refs\n"
    )
    result = _extract_contract_yaml(raw)
    assert result is not None
    assert isinstance(result["acceptance_criteria"][0], str)


def test_yaml_genuinely_broken_returns_none():
    # Malformed beyond what the preprocessor can fix
    raw = _make_fenced("acceptance_criteria:\n  - [unclosed bracket\n    key: val\n")
    result = _extract_contract_yaml(raw)
    assert result is None
