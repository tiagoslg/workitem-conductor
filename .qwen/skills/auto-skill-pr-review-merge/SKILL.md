---
name: pr-review-merge
description: Review open GitHub PRs and safely merge them, including handling stacked PRs that need a landing PR into main
source: auto-skill
extracted_at: '2026-06-15T13:04:07.627Z'
---

## Overview

Review and merge workflow for GitHub PRs using `gh` CLI. Covers code review, test verification, dependency resolution for stacked PRs, and safe merge into `main`.

## Procedure

### 1. Discover open PRs

```bash
gh pr list --state open --limit 20
```

Identify the PR(s) the user wants reviewed/merged.

### 2. Review the PR

For each PR to review, gather context:

```bash
# PR metadata, files changed, additions/deletions, commits
gh pr view <num> --json title,body,files,additions,deletions,commits,assignees,labels,state,baseRefName,headRefName,mergedAt,closedAt,comments,reviews

# The actual code diff
gh pr diff <num>
```

Review the diff for:
- **Correctness**: Does the code logic make sense?
- **Edge cases**: Are error cases handled? Timeouts on subprocess calls?
- **Tests**: Are there sufficient tests for the new functionality?
- **Scope**: Does the PR stay within its described scope?

### 3. Run tests

Use the project's test runner (check `pyproject.toml` or `Makefile` for the right command):

```bash
.venv/bin/pytest tests/ -q
```

Only proceed to merge if tests pass.

### 4. Check merge dependencies (critical for stacked PRs)

Before merging, verify the PR's base branch and any dependent PRs:

```bash
# Check what commits are ahead of the base branch
git log --oneline <baseBranch>..<headBranch>

# Check if base branch PRs are merged
gh pr list --state merged --base main --json number,title
```

Stacked PRs (PRs whose base is another feature branch, not `main`) require special handling:
- Merge the feature PR into its **base branch** first (this is GitHub's default behavior)
- If the base branch is a feature branch (not `main`), create a **follow-up PR** targeting `main` that includes all accumulated changes
- The follow-up PR should list the constituent PRs in the body

### 5. Merge the PR

```bash
gh pr merge <num> --merge --delete-branch
```

**Always use `--merge`** (create a merge commit, no squash). Use `--merge` instead of `--squash` so the PR appears as a single merge commit in history.

### 6. Handle stacked PR landing (if needed)

If the merged PR landed on a feature branch (not `main`), create a landing PR:

```bash
# 1. Check what's ahead of main
git log --oneline main..origin/<featureBranch>

# 2. Create a PR targeting main
gh pr create \
  --base main \
  --head <featureBranch> \
  --title "<summary of all merged PRs>" \
  --body "## Summary\n\nBrings the following merged PRs into \`main\`:\n- **#<n>** — <title> — merged at \`<sha>\`\n- **#<n>** — <title> — merged at \`<sha>\`\n\nAll downstream PRs are already on \`main\`."

# 3. Merge the landing PR into main
gh pr merge <landing-num> --merge --delete-branch
```

### 7. Verify clean state

```bash
git log --oneline main..origin/main   # should be empty
git status                             # should be clean
gh pr list --state open                # confirm no lingering PRs
```

## Notes

- Always verify tests pass before merging
- For stacked PRs, always create and merge the landing PR into `main` — don't leave feature branches hanging
- Merge commits (no squash) preserve the PR as a single merge commit in history
- Delete the branch after merging to keep the repo clean
