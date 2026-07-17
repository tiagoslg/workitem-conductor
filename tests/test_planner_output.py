from conductor.core.planner_output import parse_planner_output


def test_parses_full_plan():
    text = (
        "```yaml\n"
        "branch: feat/add-thing\n"
        "phases:\n"
        "  - name: setup\n"
        "    goal: scaffold the module\n"
        "    files_likely_touched:\n"
        "      - src/foo.py\n"
        "  - name: wire-up\n"
        "    goal: connect it to the CLI\n"
        "risk_level: medium\n"
        "```\n"
        "\n## Plan\n\nDo the thing.\n"
    )
    plan = parse_planner_output(text)
    assert plan is not None
    assert plan.branch == "feat/add-thing"
    assert plan.risk_level == "medium"
    assert [p.name for p in plan.phases] == ["setup", "wire-up"]
    assert plan.phases[0].goal == "scaffold the module"
    assert plan.phases[0].files_likely_touched == ["src/foo.py"]
    assert plan.phases[1].files_likely_touched == []


def test_missing_yaml_block_returns_none():
    assert parse_planner_output("just prose, no yaml block here") is None
    assert parse_planner_output("") is None


def test_malformed_yaml_returns_none():
    text = "```yaml\nbranch: [unterminated\n```\n"
    assert parse_planner_output(text) is None


def test_files_likely_touched_coerces_bad_list_items():
    text = (
        "```yaml\n"
        "branch: feat/x\n"
        "phases:\n"
        "  - name: p1\n"
        "    files_likely_touched:\n"
        "      - path with a mid-sentence: colon\n"
        "```\n"
    )
    plan = parse_planner_output(text)
    assert plan is not None
    assert plan.phases[0].files_likely_touched == ["path with a mid-sentence: colon"]


def test_no_branch_or_risk_level_still_parses():
    text = "```yaml\nphases: []\n```\n"
    plan = parse_planner_output(text)
    assert plan is not None
    assert plan.branch is None
    assert plan.risk_level is None
    assert plan.phases == []
