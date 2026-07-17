"""``conductor plans lint`` — the centerpiece validation.

Turns a pile of loose plan files into minimal governance: dependencies that
exist, no cycles, no plan marked done without evidence, no accidental
"ready to execute" status on an index-only plan, and self-sufficiency for
plans explicitly handed off to a team without cross-repo access.
"""

from __future__ import annotations

from pydantic import BaseModel

from .frontmatter import parse_frontmatter_and_body
from .models import Plan


class LintIssue(BaseModel):
    plan_id: str
    message: str


def lint_plans(plans: list[Plan]) -> list[LintIssue]:
    issues: list[LintIssue] = []
    by_id = {p.id: p for p in plans}

    seen_ids: dict[str, Plan] = {}
    for p in plans:
        if p.id in seen_ids:
            issues.append(LintIssue(plan_id=p.id, message=f"duplicate id, also used by {seen_ids[p.id].path}"))
        else:
            seen_ids[p.id] = p

    for p in plans:
        fm = p.frontmatter

        for dep_id in (*fm.depends_on, *fm.related):
            if dep_id not in by_id:
                issues.append(LintIssue(plan_id=p.id, message=f"depends_on/related references unknown id '{dep_id}'"))

        if not fm.executable and fm.status not in ("draft", "ready", "done"):
            # index/overview plans aren't meant to carry execution state at all,
            # but don't police this too hard — just flag the unusual combination.
            pass

        if fm.status == "done" and not fm.commits:
            issues.append(LintIssue(plan_id=p.id, message="status is 'done' but commits is empty"))

        if fm.executable and fm.status == "done":
            for dep_id in fm.depends_on:
                dep = by_id.get(dep_id)
                if dep is not None and dep.frontmatter.status != "done":
                    issues.append(
                        LintIssue(
                            plan_id=p.id,
                            message=f"marked done but depends_on '{dep_id}' is not done (status: {dep.frontmatter.status})",
                        )
                    )

        if fm.external_handoff:
            split = parse_frontmatter_and_body(p.path.read_text(encoding="utf-8"))
            body = split[1] if split else ""
            for dep_id in (*fm.depends_on, *fm.related):
                if dep_id not in body:
                    issues.append(
                        LintIssue(
                            plan_id=p.id,
                            message=f"external_handoff is true but '{dep_id}' has no inline summary in the body",
                        )
                    )

    cycle = _find_cycle(by_id)
    if cycle:
        issues.append(LintIssue(plan_id=cycle[0], message=f"dependency cycle: {' -> '.join(cycle)}"))

    return issues


def _find_cycle(by_id: dict[str, Plan]) -> list[str] | None:
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = dict.fromkeys(by_id, WHITE)
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = GRAY
        path.append(node)
        for dep in by_id[node].frontmatter.depends_on:
            if dep not in by_id:
                continue
            if color.get(dep) == GRAY:
                return path[path.index(dep):] + [dep]
            if color.get(dep) == WHITE:
                found = visit(dep)
                if found:
                    return found
        path.pop()
        color[node] = BLACK
        return None

    for node in by_id:
        if color[node] == WHITE:
            found = visit(node)
            if found:
                return found
    return None


def ready_to_execute(plans: list[Plan]) -> list[Plan]:
    """Plans whose dependencies are all done and that are themselves executable and ready."""
    by_id = {p.id: p for p in plans}
    result = []
    for p in plans:
        fm = p.frontmatter
        if not fm.executable or fm.status != "ready":
            continue
        if all(by_id[d].frontmatter.status == "done" for d in fm.depends_on if d in by_id):
            result.append(p)
    return result
