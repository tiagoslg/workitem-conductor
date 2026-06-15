"""The ``conductor`` command-line interface.

MVP 1 implements ``init``, ``define`` and ``status`` against an explicit state
model. ``execute`` and ``doctor`` are honest stubs that describe the roadmap so
the direction is visible without pretending to do work.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config.loader import RepoConfigError, load_repo_config, load_workspace_config
from .core.context import build_cross_project_section
from .core.engine import Engine, GoalNotApproved, StepOutcome
from .core.refine import Refiner
from .flows.loader import FlowNotFound, load_flow
from .paths import AI_DIRNAME, AiPaths, AiRootNotFound, WorkspacePaths, require_ai_paths
from .providers.registry import ProviderConfigError, build_provider, build_provider_for
from .scaffold import scaffold_ai, scaffold_workspace
from .workitems.manager import (
    approve_goal,
    create_workitem,
    get_active_id,
    list_workitems,
    load_workitem,
)
from .workspaces import (
    DEFAULT_WORKSPACE,
    WorkspaceRegistryError,
    add_project,
    list_projects,
    load_registry,
    load_workspace_paths,
    registry_path,
    remove_project,
    save_registry,
)

app = typer.Typer(
    help="Local conductor for AI-assisted development workflows.",
    no_args_is_help=True,
    add_completion=False,
)
workspace_app = typer.Typer(
    help="Manage the global workspace registry (project roots to watch).",
    no_args_is_help=True,
)
app.add_typer(workspace_app, name="workspace")
console = Console()
err_console = Console(stderr=True)

# Provider CLIs the conductor can drive (auth owned externally). Checked by `doctor`.
KNOWN_PROVIDER_CLIS = ("codex", "claude", "qwen", "ollama")


def _load_paths() -> AiPaths:
    """Locate ``.ai/`` or exit cleanly with a hint."""
    try:
        return require_ai_paths()
    except AiRootNotFound as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


def _load_ws_paths(name: str) -> WorkspacePaths:
    """Resolve a workspace name to WorkspacePaths, or exit cleanly."""
    registry = _load_registry()
    if name not in registry.workspaces:
        err_console.print(
            f"[red]Workspace '{name}' not found.[/red]  "
            f"Run `conductor workspace add <path> -w {name}` first."
        )
        raise typer.Exit(code=1)
    ws_paths = load_workspace_paths(name)
    if not ws_paths.config.exists():
        result = scaffold_workspace(ws_paths.root, name)
        for f in result.created:
            console.print(f"  [green]+[/green] {ws_paths.root}/{f}")
        console.print(
            f"  [dim]Edit [bold]{ws_paths.config}[/bold] to bind providers/roles.[/dim]\n"
        )
    return ws_paths


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
    workspace: str = typer.Option(
        None, "--workspace", "-w",
        help="Create a cross-project workitem in this workspace instead of the current repo."
    ),
) -> None:
    """Create a workitem and an editable goal contract from a goal statement."""
    if not goal or not goal.strip():
        err_console.print(
            '[red]A goal is required.[/red]  Example: conductor define "fix the policy discovery bug"'
        )
        raise typer.Exit(code=1)

    if workspace:
        paths = _load_ws_paths(workspace)
        workitem = create_workitem(paths, goal, flow="workspace-analysis")
        goal_file = workitem.directory / "goal.yml"
        console.print(f"[green]Created workspace workitem[/green] [bold]{workitem.workitem_id}[/bold]")
        console.print(f"  workspace: {workspace}  ({len(paths.project_roots)} projects)")
        console.print(f"  goal:  {goal_file}")
        console.print(
            f"\nNext: run [bold]conductor refine -w {workspace}[/bold] for cross-project analysis,\n"
            f"or edit goal.yml by hand — then [bold]conductor approve -w {workspace}[/bold]."
        )
        return

    paths = _load_paths()
    workitem = create_workitem(paths, goal)

    goal_file = workitem.directory / "goal.yml"
    rel = goal_file.relative_to(Path.cwd()) if goal_file.is_relative_to(Path.cwd()) else goal_file
    console.print(f"[green]Created workitem[/green] [bold]{workitem.workitem_id}[/bold]")
    console.print(f"  goal:  {rel}")
    console.print(f"  state: stage=[cyan]defined[/cyan] status=[yellow]draft[/yellow]")
    console.print(
        "\nNext: run [bold]conductor refine[/bold] for AI-assisted scope/criteria,\n"
        f"or edit {rel} by hand — then [bold]conductor approve[/bold] and [bold]conductor execute[/bold]."
    )


@app.command()
def approve(
    workitem_id: str = typer.Argument(
        None, help="Workitem to approve (defaults to the active one)."
    ),
    workspace: str = typer.Option(
        None, "--workspace", "-w", help="Approve a cross-project workitem in this workspace."
    ),
) -> None:
    """Approve the goal contract and mark the workitem ready to execute."""
    paths = _load_ws_paths(workspace) if workspace else _load_paths()
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
def refine(
    workitem_id: str = typer.Argument(
        None, help="Workitem to refine (defaults to the active one)."
    ),
    workspace: str = typer.Option(
        None, "--workspace", "-w",
        help="Refine a cross-project workitem in this workspace."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Force the dry-run provider (no real model)."
    ),
) -> None:
    """Improve the goal contract with an AI refiner that asks before it writes.

    The refiner (role ``refiner``) reads the goal, explores the repo, and either
    asks clarifying questions or writes scope/acceptance criteria/constraints/
    validation/stop conditions back to goal.yml. It never approves — run
    `conductor approve` after reviewing the result.

    With ``-w <workspace>``, the refiner receives cross-project context from all
    repos in the workspace so it can reason about cross-project bugs and changes.
    """
    if workspace:
        paths = _load_ws_paths(workspace)
    else:
        paths = _load_paths()

    wid = workitem_id or get_active_id(paths)
    if wid is None:
        err_console.print(
            "[red]No workitem to refine.[/red]  Run `conductor define \"<goal>\"` first."
        )
        raise typer.Exit(code=1)

    try:
        load_workitem(paths, wid)
    except FileNotFoundError:
        err_console.print(f"[red]Workitem not found:[/red] {wid}")
        raise typer.Exit(code=1)

    try:
        config = (
            load_workspace_config(paths) if workspace else load_repo_config(paths)
        )
    except RepoConfigError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    workspace_info = build_cross_project_section(paths) if workspace else None
    provider_for = build_provider_for(config, dry_run=dry_run)
    refiner = Refiner(
        paths,
        provider_for,
        max_rounds=config.refine.max_question_rounds,
        workspace_info=workspace_info,
    )

    mode = "[yellow]dry-run[/yellow]" if dry_run else (
        f"refiner from workspace config · [dim]{len(paths.project_roots)} projects[/dim]"
        if workspace else "refiner from repo.yml"
    )
    console.print(f"Refining [bold]{wid}[/bold] · {mode}\n")

    def ask(questions: list[str]) -> list[str]:
        console.print("[bold]The refiner needs some clarification:[/bold]")
        answers: list[str] = []
        for i, question in enumerate(questions, start=1):
            answers.append(typer.prompt(f"  {i}. {question}\n  >"))
        console.print("")
        return answers

    def on_round(round_no: int, kind: str) -> None:
        if kind == "questions":
            console.print(f"[dim]round {round_no}: refiner is asking questions…[/dim]")
        elif kind == "contract":
            console.print(f"[dim]round {round_no}: refiner proposed a contract.[/dim]")

    try:
        outcome = refiner.run(wid, ask=ask, on_round=on_round)
    except ProviderConfigError as exc:
        err_console.print(f"[red]Provider configuration error:[/red] {exc}")
        raise typer.Exit(code=1)

    if outcome.updated:
        goal_rel = f".ai/workitems/{wid}/goal.yml"
        console.print(
            f"\n[green]Goal contract updated.[/green]  Review/edit [bold]{goal_rel}[/bold], "
            "then run [bold]conductor approve[/bold]."
        )
    else:
        console.print(f"\n[yellow]No changes written:[/yellow] {outcome.stopped_reason}")
        if dry_run:
            console.print(
                "[dim]The dry-run provider makes no proposal — bind a real "
                "provider to the `refiner` role in repo.yml.[/dim]"
            )
        raise typer.Exit(code=1)


@app.command()
def status(
    all_: bool = typer.Option(
        False, "--all", "-a", help="List every workitem instead of just the active one."
    ),
    workspace: str = typer.Option(
        None, "--workspace", "-w", help="Show workitems from this workspace."
    ),
) -> None:
    """Show the active workitem (or all workitems with --all)."""
    paths = _load_ws_paths(workspace) if workspace else _load_paths()
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
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Force dry-run providers regardless of repo.yml."
    ),
) -> None:
    """Run the workitem flow.

    Each role is executed by the provider bound to it in repo.yml; roles with no
    binding fall back to dry-run, so an unconfigured repo still runs end-to-end.
    Use --dry-run to force dry-run everywhere.
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

    try:
        config = load_repo_config(paths)
    except RepoConfigError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    provider_for = build_provider_for(config, dry_run=dry_run)
    engine = Engine(paths, flow, provider_for=provider_for)

    mode = "[yellow]dry-run[/yellow]" if dry_run else "providers from repo.yml"
    console.print(
        f"Executing [bold]{wid}[/bold] · flow [cyan]{flow.name}[/cyan] · {mode}\n"
    )

    def on_step(step: StepOutcome) -> None:
        mark = "[green]✓[/green]" if step.ok else "[red]✗[/red]"
        rel = step.output_path.relative_to(wi.directory).as_posix()
        extra = ""
        if step.verdict and step.verdict != "unknown":
            color = "green" if step.verdict == "approved" else "yellow"
            extra = f" [{color}]{step.verdict}[/{color}]"
            if step.looped_back:
                extra += " [dim]↩ fixing[/dim]"
        console.print(
            f"  {mark} {step.role} [dim]({step.stage} · {step.provider})[/dim] → {rel}{extra}"
        )

        if step.role == "planner" and step.ok:
            output = step.output_path.read_text(encoding="utf-8")
            m = re.search(r"^BRANCH:\s*(\S+)", output, re.MULTILINE)
            if m:
                branch = m.group(1).strip()
                console.print(f"\n  [dim]Suggested branch:[/dim] [bold]{branch}[/bold]")
                if typer.confirm(f"  Create and switch to '{branch}' now?", default=True):
                    try:
                        subprocess.run(
                            ["git", "checkout", "-b", branch],
                            cwd=paths.root.parent,
                            check=True,
                            capture_output=True,
                            text=True,
                        )
                        console.print(f"  [green]✓[/green] switched to branch '{branch}'")
                    except subprocess.CalledProcessError as exc:
                        console.print(
                            f"  [yellow]![/yellow] could not create branch: "
                            f"{exc.stderr.strip() or 'already exists?'}"
                        )

    try:
        outcome = engine.run(wid, on_step=on_step)
    except GoalNotApproved as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    except ProviderConfigError as exc:
        err_console.print(f"[red]Provider configuration error:[/red] {exc}")
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

    paths: AiPaths | None = None
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

    if paths is not None:
        try:
            config = load_repo_config(paths)
        except RepoConfigError as exc:
            console.print(f"\n[red]repo.yml is invalid:[/red] {exc}")
            return
        console.print("\nRole → provider bindings (from repo.yml):")
        if not config.roles:
            console.print("  [dim]· none configured — all roles run in dry-run[/dim]")
        else:
            for role, binding in config.roles.items():
                provider_cfg = config.providers.get(binding.provider)
                if provider_cfg is None:
                    console.print(
                        f"  [red]✗[/red] {role} → {binding.provider} [red](unknown provider)[/red]"
                    )
                    continue
                avail = ""
                if provider_cfg.type == "cli_one_shot" and provider_cfg.command:
                    found = shutil.which(provider_cfg.command)
                    avail = (
                        f" [green](available)[/green]" if found else " [yellow](command not found)[/yellow]"
                    )
                elif provider_cfg.type == "api":
                    key_set = provider_cfg.api_key_env and os.environ.get(
                        provider_cfg.api_key_env
                    )
                    avail = (
                        " [green](API key set)[/green]"
                        if key_set
                        else " [yellow](API key env not set)[/yellow]"
                    )
                elif provider_cfg.type == "ollama":
                    provider = build_provider(binding.provider, provider_cfg)
                    avail = (
                        " [green](server up · model pulled)[/green]"
                        if provider.available()
                        else " [yellow](ollama not reachable / model missing)[/yellow]"
                    )
                console.print(
                    f"  [green]✓[/green] {role} → {binding.provider} "
                    f"[dim]({provider_cfg.type})[/dim]{avail}"
                )


def _load_registry():
    try:
        return load_registry()
    except WorkspaceRegistryError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


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
            "  [yellow]note:[/yellow] no .ai/ here yet — run "
            "[bold]conductor init[/bold] in that repo."
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
    """List registered workspaces and their projects."""
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
        table.add_column("workitems", justify="right")
        table.add_column("needs attention", justify="right")
        for path in ws.paths:
            root = Path(path)
            total, attention, marker = 0, 0, ""
            ai_root = root / AI_DIRNAME
            if (ai_root / "workitems").is_dir():
                paths = AiPaths(root=ai_root)
                ids = list_workitems(paths)
                total = len(ids)
                for wid in ids:
                    st = load_workitem(paths, wid).state.status
                    if st in ("needs_human", "blocked"):
                        attention += 1
            else:
                marker = " [yellow](no .ai/)[/yellow]"
            table.add_row(
                root.name + marker,
                str(root),
                str(total),
                f"[red]{attention}[/red]" if attention else "0",
            )
        console.print(table)


@app.command()
def dashboard(
    name: str = typer.Option(
        None, "--name", "-w", help="Limit to one workspace (default: all)."
    ),
    port: int = typer.Option(8787, "--port", "-p", help="Port to bind on localhost."),
    no_open: bool = typer.Option(
        False, "--no-open", help="Do not open a browser window."
    ),
) -> None:
    """Serve a read-only web dashboard of workitems across registered projects."""
    from .dashboard.server import serve

    registry = _load_registry()
    if not list_projects(registry, name):
        scope = f"workspace '{name}'" if name else "the registry"
        err_console.print(
            f"[red]No projects in {scope}.[/red]  Run "
            "`conductor workspace add <path>` first."
        )
        raise typer.Exit(code=1)
    serve(registry, workspace=name, port=port, open_browser=not no_open)


if __name__ == "__main__":
    app()
