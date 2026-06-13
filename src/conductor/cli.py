"""The ``conductor`` command-line interface.

MVP 1 implements ``init``, ``define`` and ``status`` against an explicit state
model. ``execute`` and ``doctor`` are honest stubs that describe the roadmap so
the direction is visible without pretending to do work.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .core.engine import Engine, GoalNotApproved, StepOutcome
from .flows.loader import FlowNotFound, load_flow
from .paths import AI_DIRNAME, AiPaths, AiRootNotFound, require_ai_paths
from .providers.dryrun import DryRunProvider
from .scaffold import scaffold_ai
from .workitems.manager import (
    approve_goal,
    create_workitem,
    get_active_id,
    list_workitems,
    load_workitem,
)

app = typer.Typer(
    help="Local conductor for AI-assisted development workflows.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)

# Provider CLIs the conductor can drive (auth owned externally). Checked by `doctor`.
KNOWN_PROVIDER_CLIS = ("codex", "claude", "ollama")


def _load_paths() -> AiPaths:
    """Locate ``.ai/`` or exit cleanly with a hint."""
    try:
        return require_ai_paths()
    except AiRootNotFound as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


@app.command()
def init() -> None:
    """Initialize the ``.ai/`` skeleton in the current repository."""
    root = Path.cwd() / AI_DIRNAME
    result = scaffold_ai(root)

    rel = root.relative_to(Path.cwd())
    for name in result.created:
        console.print(f"  [green]+[/green] {rel}/{name}")
    for name in result.skipped:
        console.print(f"  [dim]= {rel}/{name} (exists, kept)[/dim]")

    if result.created and not result.skipped:
        console.print(f"\n[green]Initialized {rel}/[/green]")
    elif result.created:
        console.print(f"\n[green]Updated {rel}/[/green] (existing files kept)")
    else:
        console.print(f"\n[yellow]{rel}/ already initialized[/yellow] — nothing to do")

    console.print("\nNext: [bold]conductor define \"<your goal>\"[/bold]")


@app.command()
def define(
    goal: str = typer.Argument(
        None, help="A short statement of the change you want."
    ),
) -> None:
    """Create a workitem and an editable goal contract from a goal statement."""
    if not goal or not goal.strip():
        err_console.print(
            '[red]A goal is required.[/red]  Example: conductor define "fix the policy discovery bug"'
        )
        raise typer.Exit(code=1)

    paths = _load_paths()
    workitem = create_workitem(paths, goal)

    goal_file = workitem.directory / "goal.yml"
    rel = goal_file.relative_to(Path.cwd()) if goal_file.is_relative_to(Path.cwd()) else goal_file
    console.print(f"[green]Created workitem[/green] [bold]{workitem.workitem_id}[/bold]")
    console.print(f"  goal:  {rel}")
    console.print(f"  state: stage=[cyan]defined[/cyan] status=[yellow]draft[/yellow]")
    console.print(
        "\nNext: edit the goal contract (scope, acceptance criteria, stop conditions),\n"
        f"refine {rel}, then run [bold]conductor approve[/bold] and [bold]conductor execute[/bold]."
    )


@app.command()
def approve(
    workitem_id: str = typer.Argument(
        None, help="Workitem to approve (defaults to the active one)."
    ),
) -> None:
    """Approve the goal contract and mark the workitem ready to execute."""
    paths = _load_paths()
    wid = workitem_id or get_active_id(paths)
    if wid is None:
        err_console.print(
            "[red]No workitem to approve.[/red]  Run `conductor define \"<goal>\"` first."
        )
        raise typer.Exit(code=1)

    try:
        wi = load_workitem(paths, wid)
    except FileNotFoundError:
        err_console.print(f"[red]Workitem not found:[/red] {wid}")
        raise typer.Exit(code=1)

    already_synced = wi.goal.approved and wi.state.status != "draft"
    if already_synced:
        console.print(f"[dim]{wid} is already approved and ready.[/dim]")
        return

    updated = approve_goal(paths, wid)
    console.print(f"[green]Approved[/green] [bold]{updated.workitem_id}[/bold]")
    console.print(
        f"  state: stage=[cyan]{updated.state.stage}[/cyan] "
        f"status=[yellow]{updated.state.status}[/yellow] "
        f"next=[bold]{updated.state.next_action}[/bold]"
    )
    console.print("\nNext: [bold]conductor execute[/bold]")


@app.command()
def status(
    all_: bool = typer.Option(
        False, "--all", "-a", help="List every workitem instead of just the active one."
    ),
) -> None:
    """Show the active workitem (or all workitems with --all)."""
    paths = _load_paths()
    ids = list_workitems(paths)

    if not ids:
        console.print("No workitems yet. Run [bold]conductor define \"<goal>\"[/bold].")
        return

    if all_:
        table = Table(title="Workitems")
        table.add_column("active", justify="center")
        table.add_column("id", style="bold")
        table.add_column("stage", style="cyan")
        table.add_column("status", style="yellow")
        table.add_column("next action")
        active = get_active_id(paths)
        for wid in ids:
            wi = load_workitem(paths, wid)
            marker = "[green]*[/green]" if wid == active else ""
            table.add_row(marker, wid, wi.state.stage, wi.state.status, wi.state.next_action)
        console.print(table)
        return

    active = get_active_id(paths)
    if active is None:
        console.print(
            "No active workitem set. Use [bold]conductor status --all[/bold] to list workitems."
        )
        return

    wi = load_workitem(paths, active)
    approved = "yes" if wi.goal.approved else "[red]no[/red]"
    table = Table(show_header=False, title=f"Active workitem: {wi.workitem_id}")
    table.add_column("field", style="dim")
    table.add_column("value")
    table.add_row("title", wi.state.title)
    table.add_row("flow", wi.state.flow)
    table.add_row("stage", f"[cyan]{wi.state.stage}[/cyan]")
    table.add_row("status", f"[yellow]{wi.state.status}[/yellow]")
    table.add_row("next action", wi.state.next_action)
    table.add_row("goal approved", approved)
    table.add_row("iterations", str(wi.state.iterations))
    table.add_row(
        "open issues",
        "\n".join(f"- {i}" for i in wi.state.open_issues) or "[dim]none[/dim]",
    )
    console.print(table)

    if not wi.goal.approved:
        console.print(
            "\n[dim]Goal not approved yet — refine goal.yml, then run "
            "`conductor approve`.[/dim]"
        )


@app.command()
def execute(
    workitem_id: str = typer.Argument(
        None, help="Workitem to execute (defaults to the active one)."
    ),
) -> None:
    """Run the workitem flow.

    Provider execution is dry-run for now (MVP 2 slice 1): the loop, context
    building, state transitions and artifacts are real; no model is called yet.
    Real CLI/API providers arrive in the next slice.
    """
    paths = _load_paths()
    wid = workitem_id or get_active_id(paths)
    if wid is None:
        err_console.print(
            "[red]No workitem to execute.[/red]  Run `conductor define \"<goal>\"` first."
        )
        raise typer.Exit(code=1)

    try:
        wi = load_workitem(paths, wid)
    except FileNotFoundError:
        err_console.print(f"[red]Workitem not found:[/red] {wid}")
        raise typer.Exit(code=1)

    try:
        flow = load_flow(paths, wi.state.flow)
    except FlowNotFound as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    provider = DryRunProvider()
    engine = Engine(paths, flow, provider_for=lambda role: provider)

    console.print(
        f"Executing [bold]{wid}[/bold] · flow [cyan]{flow.name}[/cyan] "
        f"· provider [yellow]{provider.name}[/yellow]\n"
    )

    def on_step(step: StepOutcome) -> None:
        mark = "[green]✓[/green]" if step.ok else "[red]✗[/red]"
        rel = step.output_path.relative_to(wi.directory).as_posix()
        console.print(f"  {mark} {step.role} [dim]({step.stage})[/dim] → {rel}")

    try:
        outcome = engine.run(wid, on_step=on_step)
    except GoalNotApproved as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    if outcome.completed:
        console.print(
            f"\n[green]Flow completed.[/green] Final report: "
            f"[bold].ai/workitems/{wid}/final_report.md[/bold]"
        )
        console.print("Review it, then accept or reopen the workitem.")
    else:
        console.print(f"\n[yellow]Stopped:[/yellow] {outcome.stopped_reason}")
        raise typer.Exit(code=1)


@app.command()
def doctor() -> None:
    """Check local prerequisites (``.ai/`` present, provider CLIs available)."""
    console.print(f"workitem-conductor [dim]v{__version__}[/dim]\n")

    try:
        paths = require_ai_paths()
        console.print(f"[green]✓[/green] .ai/ found at {paths.root}")
    except AiRootNotFound:
        console.print("[yellow]![/yellow] no .ai/ here — run `conductor init`")

    console.print("\nProvider CLIs (auth is managed by the CLI itself, not the conductor):")
    for name in KNOWN_PROVIDER_CLIS:
        path = shutil.which(name)
        if path:
            console.print(f"  [green]✓[/green] {name} [dim]({path})[/dim]")
        else:
            console.print(f"  [dim]· {name} not found[/dim]")
    console.print(
        "\n[dim]Provider execution arrives in MVP 2; these checks preview what it will use.[/dim]"
    )


if __name__ == "__main__":
    app()
