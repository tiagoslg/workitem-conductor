from conductor.core.review import parse_review_verdict


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
