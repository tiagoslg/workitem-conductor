from conductor.core.verify import parse_verify_details, parse_verify_verdict


def test_parses_passed():
    assert parse_verify_verdict("Tests all green.\nVERIFY: passed\n") == "passed"


def test_parses_failed():
    text = "Findings:\n- test_foo fails\n\nVERIFY: failed"
    assert parse_verify_verdict(text) == "failed"


def test_case_insensitive_and_indented():
    assert parse_verify_verdict("   verify: Passed") == "passed"


def test_last_verdict_wins():
    text = "VERIFY: failed\n...later...\nVERIFY: passed"
    assert parse_verify_verdict(text) == "passed"


def test_unknown_when_absent():
    assert parse_verify_verdict("no verdict here") == "unknown"
    assert parse_verify_verdict("") == "unknown"


def test_parses_details_block():
    text = (
        "VERIFY: passed\n"
        "```yaml\n"
        "tests_run: true\n"
        "tests_passed: true\n"
        "acceptance_criteria_met: true\n"
        "notes:\n"
        "  - ran the full suite\n"
        "```\n"
    )
    details = parse_verify_details(text)
    assert details is not None
    assert details.tests_run is True
    assert details.tests_passed is True
    assert details.acceptance_criteria_met is True
    assert details.notes == ["ran the full suite"]


def test_missing_details_block_returns_none():
    assert parse_verify_details("VERIFY: passed\n") is None


def test_malformed_details_block_returns_none():
    text = "VERIFY: failed\n```yaml\nnotes: [unterminated\n```\n"
    assert parse_verify_details(text) is None
