"""Unit tests for stop_conditions.py: deterministic backstops, the
``STOP:`` marker parser, and the status/reason back-compat properties
``core/workspace_engine.py`` still relies on.
"""

from conductor.core.stop_conditions import (
    check_global_cap,
    check_max_fix_iterations,
    parse_stop_signal,
    status_for,
)


# --- status_for ---

def test_status_for_provider_error_is_blocked():
    assert status_for("provider_error") == "blocked"


def test_status_for_semantic_types_is_needs_human():
    for stop_type in ("scope_change", "secrets_access", "dangerous_command",
                       "production_access", "global_cap", "fix_loop_exhausted", "other"):
        assert status_for(stop_type) == "needs_human"


# --- check_global_cap / check_max_fix_iterations: structured + back-compat ---

def test_check_global_cap_stop_reason_and_back_compat():
    decision = check_global_cap(50, cap=50)
    assert decision.stop is True
    assert decision.stop_reason.type == "global_cap"
    assert "50" in decision.stop_reason.message
    # back-compat properties used by core/workspace_engine.py
    assert decision.reason == decision.stop_reason.message
    assert decision.status == "needs_human"


def test_check_global_cap_go():
    decision = check_global_cap(1, cap=50)
    assert decision.stop is False
    assert decision.stop_reason is None
    assert decision.reason == ""
    assert decision.status == "needs_human"


def test_check_max_fix_iterations_stop_reason_and_back_compat():
    decision = check_max_fix_iterations(3, 3)
    assert decision.stop is True
    assert decision.stop_reason.type == "fix_loop_exhausted"
    assert decision.reason == decision.stop_reason.message
    assert decision.status == "needs_human"


def test_check_max_fix_iterations_go():
    decision = check_max_fix_iterations(1, 3)
    assert decision.stop is False


# --- parse_stop_signal ---

def test_parse_stop_signal_scope_change():
    output = "STOP: scope_change\nThis touches files outside the approved scope.\n"
    reason = parse_stop_signal(output)
    assert reason is not None
    assert reason.type == "scope_change"
    assert "outside the approved scope" in reason.message


def test_parse_stop_signal_all_known_types():
    for stop_type in ("scope_change", "secrets_access", "dangerous_command", "production_access"):
        output = f"STOP: {stop_type}\nreason text\n"
        reason = parse_stop_signal(output)
        assert reason is not None
        assert reason.type == stop_type


def test_parse_stop_signal_none_when_no_marker():
    assert parse_stop_signal("just a normal plan\nno markers here\n") is None


def test_parse_stop_signal_unknown_type_returns_none():
    # only the 4 known types are recognized — anything else is not a marker
    assert parse_stop_signal("STOP: something_else\nreason\n") is None


def test_parse_stop_signal_case_insensitive_and_anywhere_in_output():
    output = "## Plan\n\nstop: SECRETS_ACCESS\nneeds an api key\n"
    reason = parse_stop_signal(output)
    assert reason is not None
    assert reason.type == "secrets_access"


def test_parse_stop_signal_extracts_evidence_bullets():
    output = (
        "STOP: dangerous_command\n"
        "Would require deleting production data.\n"
        "- rm -rf /data/prod\n"
        "- DROP TABLE users;\n"
    )
    reason = parse_stop_signal(output)
    assert reason is not None
    assert "deleting production data" in reason.message
    assert reason.evidence == ["rm -rf /data/prod", "DROP TABLE users;"]


def test_parse_stop_signal_defaults_message_when_marker_has_no_text():
    reason = parse_stop_signal("STOP: production_access\n")
    assert reason is not None
    assert reason.type == "production_access"
    assert "production_access" in reason.message
