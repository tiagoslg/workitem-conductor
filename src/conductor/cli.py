"""The ``conductor`` command-line interface.

conductor is a control-plane/audit layer over ``execution_plans/*.md`` files —
it never calls a model and never executes anything. Plans are authored by
OpenCode's ``plan-writer`` agent (or by hand) and executed by OpenCode's
``/implement-plan``; this CLI only scans, validates, tracks status, and
reports on the frontmatter those plans already carry. See
``docs/audit-layer-pivot.md`` for the full design rationale.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .plans.lint import lint_plans, ready_to_execute
from .plans.opencode_db import OpenCodeDbNotFound
from .plans.scan import resolve_repos, scan_repos
from .plans.sync import read_snapshot, sync_plan
from .plans.write import PlanFileError, mark_done, mark_ready
from .workspaces import (
    DEFAULT_WORKSPACE,
    WorkspaceRegistryError,
    add_project,
    list_projects,
    load_registry,
    registry_path,
    remove_project,
    save_registry,
)

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="A local control-plane/audit layer over execution_plans/*.md files.",
)
workspace_app = typer.Typer(add_completion=False, no_args_is_help=True)
plans_app = typer.Typer(add_completion=False, no_args_is_help=True)
app.add_typer(workspace_app, name="workspace")
app.add_typer(plans_app, name="plans")


def _load_registry():
    try:
        return load_registry()
    except WorkspaceRegistryError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


# --------------------------------------------------------------------------
# workspace
# --------------------------------------------------------------------------


@workspace_app.command("add")
def workspace_add(
    path: str = typer.Argument(".", help="Project root to register (default: cwd)."),
    name: str = typer.Option(
        DEFAULT_WORKSPACE, "--name", "-w", help="Workspace to add it to."
    ),
) -> None:
    """Register a project root in the global workspace registry."""
    registry = _load_registry()
    resolved, added, has_ai = add_project(registry, path, workspace=name)
    save_registry(registry)
    if added:
        console.print(f"[green]Added[/green] {resolved} [dim]→ workspace '{name}'[/dim]")
    else:
        console.print(f"[dim]{resolved} already in workspace '{name}'[/dim]")
    if not has_ai:
        console.print(
            "  [yellow]note:[/yellow] no .ai/ here yet — plans won't be found "
            "until an execution_plans/ directory exists."
        )


@workspace_app.command("remove")
def workspace_remove(
    path: str = typer.Argument(..., help="Project root to remove."),
    name: str = typer.Option(
        None, "--name", "-w", help="Workspace to remove from (default: all)."
    ),
) -> None:
    """Remove a project root from the registry."""
    registry = _load_registry()
    if remove_project(registry, path, workspace=name):
        save_registry(registry)
        console.print(f"[green]Removed[/green] {Path(path).expanduser().resolve()}")
    else:
        console.print("[yellow]Not found in the registry.[/yellow]")


@workspace_app.command("list")
def workspace_list() -> None:
    """List registered workspaces and their projects, with a plan count each."""
    registry = _load_registry()
    any_project = any(ws.paths for ws in registry.workspaces.values())
    if not any_project:
        console.print(
            f"No projects registered. Run [bold]conductor workspace add .[/bold]\n"
            f"[dim]registry: {registry_path()}[/dim]"
        )
        return

    for ws_name, ws in registry.workspaces.items():
        if not ws.paths:
            continue
        table = Table(title=f"workspace: {ws_name}")
        table.add_column("project", style="bold")
        table.add_column("path", style="dim")
        table.add_column("plans", justify="right")
        for path in ws.paths:
            root = Path(path)
            plans, _warnings = scan_repos({root.name: root})
            marker = "" if (root / ".ai" / "execution_plans").is_dir() else " [yellow](no execution_plans/)[/yellow]"
            table.add_row(root.name + marker, str(root), str(len(plans)))
        console.print(table)


# --------------------------------------------------------------------------
# plans
# --------------------------------------------------------------------------


def _scan(workspace: str | None, repo_filter: str | None) -> tuple[list, list]:
    repos = resolve_repos(workspace)
    if repo_filter:
        repos = {n: p for n, p in repos.items() if n == repo_filter}
    return scan_repos(repos)


def _print_warnings(warnings: list) -> None:
    for w in warnings:
        err_console.print(f"[yellow]warning:[/yellow] {w.path}: {w.reason}")


@plans_app.command("list")
def plans_list(
    sprint: str = typer.Option(None, "--sprint", help="Only plans in this sprint."),
    repo: str = typer.Option(None, "--repo", help="Only plans in this repo."),
    workspace: str = typer.Option(None, "--workspace", "-w", help="Registered workspace to scan (default: all)."),
    with_execution: bool = typer.Option(
        False, "--with-execution", help="Add columns from the last `plans sync` snapshot (sessions, tokens)."
    ),
) -> None:
    """List execution plans across registered repos (plus cwd)."""
    plans, warnings = _scan(workspace, repo)
    _print_warnings(warnings)
    if sprint is not None:
        plans = [p for p in plans if p.frontmatter.sprint == sprint]
    if not plans:
        console.print("[dim]No plans found.[/dim]")
        return

    table = Table()
    table.add_column("id", style="bold")
    table.add_column("repo")
    table.add_column("sprint", style="dim")
    table.add_column("status")
    table.add_column("depends_on", style="dim")
    table.add_column("commits", style="dim")
    if with_execution:
        table.add_column("sessions", justify="right")
        table.add_column("tokens", justify="right")
    missing_snapshot = False
    for p in sorted(plans, key=lambda p: (p.repo, p.id)):
        fm = p.frontmatter
        status_style = {
            "draft": "dim",
            "ready": "cyan",
            "in_progress": "yellow",
            "blocked": "red",
            "done": "green",
            "canceled": "dim strike",
        }.get(fm.status, "")
        row = [
            fm.id,
            p.repo,
            fm.sprint or "",
            f"[{status_style}]{fm.status}[/{status_style}]" if status_style else fm.status,
            ", ".join(fm.depends_on),
            ", ".join(fm.commits),
        ]
        if with_execution:
            snap = read_snapshot(fm.id)
            if snap is None:
                missing_snapshot = True
                row += ["[dim]-[/dim]", "[dim]-[/dim]"]
            else:
                row += [
                    str(snap.totals.session_count),
                    f"{snap.totals.tokens_input}+{snap.totals.tokens_output}",
                ]
        table.add_row(*row)
    console.print(table)
    if with_execution and missing_snapshot:
        console.print("[dim]Run `conductor plans sync` for execution data on the plans marked '-'.[/dim]")


@plans_app.command("ready")
def plans_ready(
    workspace: str = typer.Option(None, "--workspace", "-w"),
    repo: str = typer.Option(None, "--repo"),
) -> None:
    """Plans that are status=ready with every dependency already done."""
    plans, warnings = _scan(workspace, repo)
    _print_warnings(warnings)
    plans = ready_to_execute(plans)
    if not plans:
        console.print("[dim]Nothing is ready to execute.[/dim]")
        return
    for p in sorted(plans, key=lambda p: (p.repo, p.id)):
        console.print(f"[green]{p.id}[/green] [dim]({p.repo})[/dim] — {p.path}")


@plans_app.command("lint")
def plans_lint(
    plan_id: str = typer.Argument(None, help="Only lint this plan id (default: everything found)."),
    workspace: str = typer.Option(None, "--workspace", "-w"),
    repo: str = typer.Option(None, "--repo"),
) -> None:
    """Validate dependencies, cycles, self-sufficiency, and status/evidence consistency."""
    plans, warnings = _scan(workspace, repo)
    _print_warnings(warnings)
    issues = lint_plans(plans)
    if plan_id is not None:
        issues = [i for i in issues if i.plan_id == plan_id]
    if not issues and not warnings:
        console.print("[green]No issues found.[/green]")
        return
    for issue in issues:
        console.print(f"[red]✗[/red] [bold]{issue.plan_id}[/bold]: {issue.message}")
    if issues:
        raise typer.Exit(code=1)


@plans_app.command("mark")
def plans_mark(
    plan_id: str = typer.Argument(..., help="Plan id to update."),
    status: str = typer.Argument(..., help="'ready' or 'done'."),
    commit: str = typer.Option(None, "--commit", help="Commit sha to append (only for 'done')."),
    workspace: str = typer.Option(None, "--workspace", "-w"),
) -> None:
    """Transition a plan's status. The plan file itself is the source of truth — no separate database."""
    if status not in ("ready", "done"):
        err_console.print("[red]status must be 'ready' or 'done'[/red]")
        raise typer.Exit(code=1)
    plans, _warnings = _scan(workspace, None)
    matches = [p for p in plans if p.id == plan_id]
    if not matches:
        err_console.print(f"[red]No plan with id '{plan_id}' found.[/red]")
        raise typer.Exit(code=1)
    plan = matches[0]
    try:
        if status == "ready":
            mark_ready(plan.path)
        else:
            mark_done(plan.path, commit=commit)
    except PlanFileError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]{plan_id}[/green] → {status}")


@plans_app.command("sync")
def plans_sync(
    plan_id: str = typer.Argument(None, help="Only sync this plan id (default: everything found)."),
    workspace: str = typer.Option(None, "--workspace", "-w"),
    repo: str = typer.Option(None, "--repo"),
    db: str = typer.Option(None, "--db", help="Path to opencode.db (default: ~/.local/share/opencode/opencode.db)."),
) -> None:
    """Correlate plans with opencode.db sessions (via the PLAN_ID: marker) and snapshot the result."""
    plans, warnings = _scan(workspace, repo)
    _print_warnings(warnings)
    if plan_id is not None:
        plans = [p for p in plans if p.id == plan_id]
        if not plans:
            err_console.print(f"[red]No plan with id '{plan_id}' found.[/red]")
            raise typer.Exit(code=1)
    if not plans:
        console.print("[dim]No plans found.[/dim]")
        return

    db_path = Path(db) if db else None
    for p in sorted(plans, key=lambda p: (p.repo, p.id)):
        try:
            snap = sync_plan(p, db_path=db_path)
        except OpenCodeDbNotFound as exc:
            err_console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from exc
        if snap.matched_via == "none":
            console.print(f"[dim]{p.id} — no matching sessions found[/dim]")
        else:
            t = snap.totals
            console.print(
                f"[green]{p.id}[/green] — {t.session_count} sessions, "
                f"{t.tokens_input}+{t.tokens_output} tokens (marker)"
            )


@plans_app.command("table")
def plans_table(
    sprint: str = typer.Option(None, "--sprint"),
    workspace: str = typer.Option(None, "--workspace", "-w"),
) -> None:
    """Regenerate a markdown table for a sprint — paste into a PR or share with another team."""
    plans, warnings = _scan(workspace, None)
    _print_warnings(warnings)
    if sprint is not None:
        plans = [p for p in plans if p.frontmatter.sprint == sprint]
    if not plans:
        console.print("[dim]No plans found.[/dim]")
        return

    lines = ["| id | repo | status | depends_on | commits |", "|---|---|---|---|---|"]
    for p in sorted(plans, key=lambda p: (p.repo, p.id)):
        fm = p.frontmatter
        lines.append(
            f"| {fm.id} | {p.repo} | {fm.status} | {', '.join(fm.depends_on)} | {', '.join(fm.commits)} |"
        )
    print("\n".join(lines))


if __name__ == "__main__":
    app()
