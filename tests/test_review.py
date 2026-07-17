from conductor.core.review import parse_review_details, parse_review_verdict


def test_parses_approved():
    assert parse_review_verdict("Looks good.\nREVIEW: approved\n") == "approved"


def test_parses_changes_requested():
    text = "Issues:\n- foo\n- bar\n\nREVIEW: changes_requested"
    assert parse_review_verdict(text) == "changes_requested"


def test_case_insensitive_and_indented():
    assert parse_review_verdict("   review: Approved") == "approved"


def test_last_verdict_wins():
    text = "REVIEW: changes_requested\n...later...\nREVIEW: approved"
    assert parse_review_verdict(text) == "approved"


def test_unknown_when_absent():
    assert parse_review_verdict("no verdict here") == "unknown"
    assert parse_review_verdict("") == "unknown"


def test_parses_details_block():
    text = (
        "REVIEW: changes_requested\n"
        "```yaml\n"
        "confidence: 0.8\n"
        "blocking_issues:\n"
        "  - missing null check\n"
        "non_blocking_issues:\n"
        "  - could rename this var\n"
        "suggested_next_role: implementer\n"
        "```\n"
    )
    details = parse_review_details(text)
    assert details is not None
    assert details.confidence == 0.8
    assert details.blocking_issues == ["missing null check"]
    assert details.non_blocking_issues == ["could rename this var"]
    assert details.suggested_next_role == "implementer"


def test_missing_details_block_returns_none():
    assert parse_review_details("REVIEW: approved\n") is None


def test_malformed_details_block_returns_none():
    text = "REVIEW: changes_requested\n```yaml\nblocking_issues: [unterminated\n```\n"
    assert parse_review_details(text) is None
