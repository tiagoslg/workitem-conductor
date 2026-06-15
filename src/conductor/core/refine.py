"""AI-assisted goal definition: the ``conductor refine`` loop.

Providers are stateless one-shot CLIs, so an interactive clarification dialogue
is run as a *re-prompt loop with a deterministic gate*, the same shape as the
review gate in ``review.py``. Each round the refiner is given the goal plus the
Q&A transcript so far and must emit exactly one marker:

    QUESTIONS:        -> a list of clarifying questions; we ask the human and loop
    CONTRACT:         -> a fenced YAML block of contract fields; we write goal.yml

This keeps the dialogue testable and works with any backend, with no persistent
session (that arrives with MVP 3).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

import yaml
from pydantic import ValidationError

from ..paths import AiPaths
from ..providers.base import Provider, ProviderRequest
from ..workitems.manager import Workitem, load_workitem, save_goal, save_state
from ..workitems.models import GoalContract, utcnow_iso
from .context import load_role_prompt

#: Resolves the provider for a role, exactly as the engine consumes it.
ProviderFor = Callable[[str], Provider]
#: Given the refiner's questions, return one answer per question.
AnswerFn = Callable[[list[str]], list[str]]

DEFAULT_MAX_ROUNDS = 5

#: Contract fields the refiner may propose. ``goal`` and ``approved`` are never
#: touched by refine — it proposes scope, it does not restate intent or approve.
_CONTRACT_FIELDS = (
    "scope",
    "acceptance_criteria",
    "constraints",
    "validation",
    "stop_conditions",
)

# Markers are matched leniently: smaller models often wrap them in markdown
# (`**CONTRACT:**`, `## QUESTIONS`, `` `CONTRACT` ``) or drop the colon. We allow
# leading decoration and an optional trailing colon.
_CONTRACT_RE = re.compile(r"(?im)^[ \t>#*`]*CONTRACT\b[ \t]*:?")
_QUESTIONS_RE = re.compile(r"(?im)^[ \t>#*`]*QUESTIONS\b[ \t]*:?")
_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_LIST_MARKER_RE = re.compile(r"^(?:\d+[.)]|[-*])\s*")
_CONTRACT_KEYS = set(_CONTRACT_FIELDS)


@dataclass
class RefineResponse:
    """Parsed refiner output. ``kind`` is questions | contract | unknown."""

    kind: str
    questions: list[str] = field(default_factory=list)
    contract: dict | None = None


def _preprocess_yaml(text: str) -> str | None:
    """Quote list-item values that contain YAML flow indicators or bare colons.

    Handles the two most common model mistakes in contract YAML:
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


def _contract_list_items_are_strings(data: dict) -> bool:
    """Return True if all list-valued contract fields contain only strings.

    When a model writes ``at minimum: broken refs`` in a list item, YAML parses
    it silently as ``{'at minimum': 'broken refs'}`` — no YAMLError, wrong type.
    """
    for field in ("acceptance_criteria", "constraints", "validation", "stop_conditions"):
        for item in data.get(field) or []:
            if not isinstance(item, str):
                return False
    return True


def _extract_contract_yaml(after: str) -> dict | None:
    """Parse the fenced YAML block following ``CONTRACT:`` into a dict, or None.

    Tries the raw block first. If the parse fails (YAMLError) *or* succeeds but
    list items are dicts instead of strings (silent misparse of bare colons),
    runs a preprocessing pass that quotes offending values and retries.
    """
    fence = _FENCE_RE.search(after)
    raw = fence.group(1) if fence else after

    try:
        data = yaml.safe_load(raw)
        if isinstance(data, dict) and _contract_list_items_are_strings(data):
            return data
    except yaml.YAMLError:
        pass

    preprocessed = _preprocess_yaml(raw)
    if preprocessed is None:
        return None
    try:
        data = yaml.safe_load(preprocessed)
        if isinstance(data, dict):
            return data
    except yaml.YAMLError:
        pass
    return None


def _extract_questions(after: str) -> list[str]:
    """Collect the question lines following ``QUESTIONS:`` (list markers stripped)."""
    questions: list[str] = []
    for line in after.splitlines():
        stripped = line.strip()
        if not stripped:
            if questions:
                break
            continue
        cleaned = _LIST_MARKER_RE.sub("", stripped).strip()
        if cleaned:
            questions.append(cleaned)
    return questions


def _infer_contract(text: str) -> dict | None:
    """Find any fenced block that parses as a contract (has contract keys).

    Lets a model that wrote the YAML without the literal ``CONTRACT:`` marker
    still be understood, without misreading an unrelated code block.
    """
    for fence in _FENCE_RE.finditer(text):
        try:
            data = yaml.safe_load(fence.group(1))
        except yaml.YAMLError:
            continue
        if isinstance(data, dict) and (_CONTRACT_KEYS & set(data)):
            return data
    return None


def _infer_questions(text: str) -> list[str]:
    """Read a marker-less list of questions: list items ending in ``?``.

    Requires a list marker and a question mark so prose sentences are not
    mistaken for questions.
    """
    questions: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.endswith("?") and _LIST_MARKER_RE.match(stripped):
            questions.append(_LIST_MARKER_RE.sub("", stripped).strip())
    return questions


def parse_refine_response(text: str) -> RefineResponse:
    """Classify a refiner response, tolerant of models that follow the gate
    loosely. Contract wins over questions. Order: explicit ``CONTRACT:`` marker →
    inferred contract (a YAML block with contract keys) → explicit ``QUESTIONS:``
    marker → inferred questions (a ``?``-terminated list) → ``unknown``."""
    text = text or ""

    contract_marker = _CONTRACT_RE.search(text)
    if contract_marker:
        contract = _extract_contract_yaml(text[contract_marker.end():])
        if contract is not None:
            return RefineResponse(kind="contract", contract=contract)

    inferred_contract = _infer_contract(text)
    if inferred_contract is not None:
        return RefineResponse(kind="contract", contract=inferred_contract)

    questions_marker = _QUESTIONS_RE.search(text)
    if questions_marker:
        questions = _extract_questions(text[questions_marker.end():])
        if questions:
            return RefineResponse(kind="questions", questions=questions)

    inferred_questions = _infer_questions(text)
    if inferred_questions:
        return RefineResponse(kind="questions", questions=inferred_questions)

    return RefineResponse(kind="unknown")


def build_refine_context(paths: AiPaths, workitem: Workitem, transcript: str) -> str:
    """Compose the prompt for one refine round: role + repo instructions + goal +
    the Q&A so far. Distinct from the execution context, which replays step
    outputs (irrelevant before any step has run)."""
    parts: list[str] = [load_role_prompt(paths, "refiner").rstrip()]

    if paths.instructions.is_file():
        parts.append("\n---\n## Repository instructions\n")
        parts.append(paths.instructions.read_text(encoding="utf-8").rstrip())

    parts.append("\n---\n## Workitem\n")
    parts.append(f"- id: {workitem.workitem_id}")
    parts.append(f"- title: {workitem.state.title}")

    parts.append("\n## Current goal contract\n")
    parts.append("```yaml\n" + workitem.goal.to_yaml().rstrip() + "\n```")

    if transcript.strip():
        parts.append("\n## Clarification so far\n")
        parts.append(transcript.rstrip())

    parts.append(
        "\n## Your task\n"
        "Explore the repository as needed, then emit either a QUESTIONS: block "
        "(to ask the human) or a CONTRACT: block (to write the contract), exactly "
        "as described above."
    )
    return "\n".join(parts) + "\n"


def _format_qa(round_no: int, questions: list[str], answers: list[str]) -> str:
    lines = [f"### Round {round_no}"]
    for i, question in enumerate(questions):
        answer = answers[i] if i < len(answers) else ""
        lines.append(f"Q: {question}")
        lines.append(f"A: {answer}")
    return "\n".join(lines)


def _merge_contract(goal: GoalContract, proposed: dict) -> GoalContract:
    """Return a new contract with proposed fields applied, goal/approved kept."""
    merged = goal.model_dump()
    for key in _CONTRACT_FIELDS:
        value = proposed.get(key)
        if value is not None:
            merged[key] = value
    merged["goal"] = goal.goal
    merged["approved"] = goal.approved
    return GoalContract.model_validate(merged)


@dataclass
class RefineOutcome:
    workitem_id: str
    rounds: int = 0  # provider calls made
    updated: bool = False
    stopped_reason: str | None = None


class Refiner:
    """Drives the question/contract loop for one workitem's goal."""

    def __init__(
        self,
        paths: AiPaths,
        provider_for: ProviderFor,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
    ) -> None:
        self.paths = paths
        self.provider_for = provider_for
        self.max_rounds = max_rounds

    def run(
        self,
        workitem_id: str,
        *,
        ask: AnswerFn,
        on_round: Callable[[int, str], None] | None = None,
    ) -> RefineOutcome:
        wi = load_workitem(self.paths, workitem_id)
        outcome = RefineOutcome(workitem_id=workitem_id)
        provider = self.provider_for("refiner")
        transcript_parts: list[str] = []
        question_rounds = 0

        while True:
            outcome.rounds += 1
            prompt = build_refine_context(
                self.paths, wi, "\n\n".join(transcript_parts)
            )
            result = provider.run(
                ProviderRequest(
                    role="refiner",
                    prompt=prompt,
                    workitem_id=workitem_id,
                    cwd=self.paths.root.parent,
                )
            )
            response_kind = (
                "failed" if not result.ok else parse_refine_response(result.output).kind
            )
            # Always persist the raw round so a stop is diagnosable later.
            self._save_raw(wi, outcome.rounds, result, response_kind)

            if not result.ok:
                return self._stop(
                    wi,
                    outcome,
                    f"refiner provider failed: {result.error or 'unknown error'}",
                )

            response = parse_refine_response(result.output)
            if on_round:
                on_round(outcome.rounds, response.kind)

            if response.kind == "contract":
                try:
                    new_goal = _merge_contract(wi.goal, response.contract or {})
                except ValidationError as exc:
                    return self._stop(
                        wi, outcome, f"refiner proposed an invalid contract: {exc}"
                    )
                self._apply(
                    wi,
                    new_goal,
                    "\n\n".join(transcript_parts),
                    provider.name,
                    question_rounds,
                )
                outcome.updated = True
                return outcome

            if response.kind == "unknown":
                return self._stop(
                    wi,
                    outcome,
                    "refiner returned no QUESTIONS:/CONTRACT: marker "
                    f"(provider: {result.provider})",
                )

            # questions
            if question_rounds >= self.max_rounds:
                return self._stop(
                    wi,
                    outcome,
                    f"reached max question rounds ({self.max_rounds}) "
                    "without a contract",
                )
            question_rounds += 1
            answers = ask(response.questions)
            transcript_parts.append(
                _format_qa(question_rounds, response.questions, answers)
            )

    def _apply(
        self,
        wi: Workitem,
        new_goal: GoalContract,
        transcript: str,
        provider_name: str,
        question_rounds: int,
    ) -> None:
        save_goal(self.paths, wi.workitem_id, new_goal)
        self._write_refine_md(wi, new_goal, transcript, provider_name, question_rounds)
        wi.state.artifacts["refine"] = "refine.md"
        wi.state.record(
            f"goal refined via {provider_name} ({question_rounds} question rounds)"
        )
        save_state(self.paths, wi.state)

    def _write_refine_md(
        self,
        wi: Workitem,
        new_goal: GoalContract,
        transcript: str,
        provider_name: str,
        question_rounds: int,
    ) -> None:
        body = [
            f"# Goal refinement — {wi.workitem_id}",
            "",
            f"- generated: {utcnow_iso()}",
            f"- provider: {provider_name}",
            f"- question rounds: {question_rounds}",
            "",
            "## Clarification",
            "",
            transcript.strip() or "_(none — contract written without questions)_",
            "",
            "## Resulting goal contract",
            "",
            "```yaml",
            new_goal.to_yaml().rstrip(),
            "```",
            "",
        ]
        (wi.directory / "refine.md").write_text("\n".join(body), encoding="utf-8")

    def _save_raw(self, wi: Workitem, n: int, result, kind: str) -> None:
        """Persist one round's raw provider output for diagnosis.

        Written every round (success or stop) so a "no marker" failure can be
        inspected after the fact instead of being lost.
        """
        directory = wi.directory / "refine"
        directory.mkdir(parents=True, exist_ok=True)
        header = (
            f"# refine round {n} — provider: {result.provider} — "
            f"ok: {result.ok} — parsed: {kind}\n\n"
        )
        if result.error:
            header += f"> error: {result.error}\n\n"
        body = result.output if result.output else "_(empty output)_"
        (directory / f"{n:02d}-raw.md").write_text(header + body, encoding="utf-8")

    def _stop(self, wi: Workitem, outcome: RefineOutcome, reason: str) -> RefineOutcome:
        outcome.stopped_reason = reason
        wi.state.record(f"refine stopped: {reason}")
        save_state(self.paths, wi.state)
        return outcome
