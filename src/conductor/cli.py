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
import threading
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.status import Status as _RichStatus
from rich.table import Table

from . import __version__
from .config.loader import (
    RepoConfigError,
    _provider_to_dict,
    load_global_defaults,
    load_repo_config,
    load_workspace_config,
    save_global_defaults,
)
from .config.models import ProviderConfig, RepoConfig, RoleBinding
from .core.context import build_cross_project_section
from .core.engine import Engine, GoalNotApproved, StepOutcome
from .core.refine import Refiner
from .core.workspace_engine import ProjectStepOutcome, WorkspaceEngine
from .core.worktree import (
    branch_name as worktree_branch,
    commit_worktree,
    create_worktree,
    merge_worktree,
    remove_worktree,
    worktree_path,
)
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
    reopen_workitem,
    save_state,
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

_CONVENTIONAL_PREFIXES = ("feat", "fix", "chore", "refactor", "docs", "test", "style", "perf")


def _build_commit_msg(title: str, feature_branch: str | None, workitem_id: str) -> str:
    """Conventional commit message derived from the feature branch name."""
    prefix = ""
    if feature_branch and "/" in feature_branch:
        kind = feature_branch.split("/")[0]
        if kind in _CONVENTIONAL_PREFIXES:
            prefix = f"{kind}: "
    return f"{prefix}{title}\n\nWorkitem: {workitem_id}"


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


class _SpinnerGuard:
    """Shows a rich spinner with a live elapsed timer between provider calls.

    A background thread updates the status text every second so the counter
    ticks while the main thread is blocked on a subprocess call.

    When ``stream=True``, the spinner is suppressed and only time tracking is
    active — elapsed time is still available via ``stop()`` for completion lines.
    """

    def __init__(self, stream: bool = False) -> None:
        self._stream = stream
        self._status: _RichStatus | None = None
        self._t0: float = 0.0
        self._base_text: str = ""
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None

    def _fmt_elapsed(self) -> str:
        s = int(time.monotonic() - self._t0)
        m, r = divmod(s, 60)
        return f"{m}m {r:02d}s" if m else f"{s}s"

    def _updater(self) -> None:
        while not self._stop_evt.wait(timeout=1.0):
            if self._status:
                self._status.update(
                    f"{self._base_text}  [dim]{self._fmt_elapsed()}[/dim]"
                )

    def start(self, text: str) -> None:
        self._t0 = time.monotonic()
        if self._stream:
            return

        # Tear down any previous spinner + timer thread.
        self._stop_evt.set()
        if self._thread:
            self._thread.join()
        if self._status:
            self._status.stop()

        self._base_text = text
        self._stop_evt.clear()

        self._status = _RichStatus(text, console=console, spinner="dots")
        self._status.start()

        self._thread = threading.Thread(target=self._updater, daemon=True)
        self._thread.start()

    def stop(self) -> float:
        """Stop the spinner and return elapsed seconds since start()."""
        elapsed = time.monotonic() - self._t0
        if not self._stream:
            self._stop_evt.set()
            if self._thread:
                self._thread.join()
                self._thread = None
            if self._status:
                self._status.stop()
                self._status = None
        return elapsed

    def __enter__(self) -> "_SpinnerGuard":
        return self

    def __exit__(self, *_) -> None:
        self.stop()


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


def _ensure_ai_in_gitignore(project_root: Path) -> str:
    """Add ``.ai/`` to the project's root .gitignore if not already present.

    Returns ``"added"`` if the entry was appended to an existing file,
    ``"created"`` if a new .gitignore was created, or ``"exists"`` if the
    entry was already there.
    """
    gitignore = project_root / ".gitignore"
    entry = f"{AI_DIRNAME}/"

    if gitignore.is_file():
        content = gitignore.read_text(encoding="utf-8")
        lines = content.splitlines()
        if any(line.strip().rstrip("/") == AI_DIRNAME.rstrip("/") for line in lines):
            return "exists"
        separator = "\n" if content and not content.endswith("\n") else ""
        gitignore.write_text(
            content + separator + f"\n# workitem-conductor\n{entry}\n",
            encoding="utf-8",
        )
        return "added"

    gitignore.write_text(f"# workitem-conductor\n{entry}\n", encoding="utf-8")
    return "created"


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

    gitignore_status = _ensure_ai_in_gitignore(Path.cwd())
    if gitignore_status == "added":
        console.print(f"  [green]+[/green] .gitignore ← added [bold]{AI_DIRNAME}/[/bold]")
    elif gitignore_status == "created":
        console.print(f"  [green]+[/green] .gitignore (created) ← added [bold]{AI_DIRNAME}/[/bold]")

    from .workspaces import global_defaults_path
    has_globals = global_defaults_path().is_file()
    if has_globals:
        console.print(
            "\n[dim]Global defaults detected — providers/roles inherited automatically.[/dim]"
        )
        console.print("Next: [bold]conductor define \"<your goal>\"[/bold]")
    else:
        console.print(
            "\nNext: [bold]conductor config --global[/bold]  "
            "[dim](once per machine — sets provider defaults for all repos)[/dim]"
        )
        console.print("Then: [bold]conductor define \"<your goal>\"[/bold]")


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

    spinner = _SpinnerGuard()

    def on_round_start(round_no: int) -> None:
        spinner.start(f"  [cyan]refiner[/cyan] [dim]round {round_no}…[/dim]")

    def on_round(round_no: int, kind: str) -> None:
        elapsed = spinner.stop()
        if kind == "questions":
            console.print(f"[dim]round {round_no}: refiner is asking questions… ({elapsed:.0f}s)[/dim]")
        elif kind == "contract":
            console.print(f"[dim]round {round_no}: refiner proposed a contract. ({elapsed:.0f}s)[/dim]")

    try:
        with spinner:
            outcome = refiner.run(wid, ask=ask, on_round=on_round, on_round_start=on_round_start)
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
    workspace: str = typer.Option(
        None, "--workspace", "-w",
        help="Execute a cross-project workitem in this workspace."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Force dry-run providers regardless of config."
    ),
    stream: bool = typer.Option(
        False, "--stream", "-s",
        help="Stream provider stdout live instead of showing a spinner. Useful for spotting loops.",
    ),
) -> None:
    """Run the workitem flow.

    With ``-w <workspace>``, runs the two-phase workspace flow: planner once
    (cross-project context), then implementer + reviewer per target project.
    Without ``-w``, runs the single-repo flow as usual.

    Use ``--stream`` to see provider output in real time — helpful for slow models
    like Qwen that can loop silently.
    """
    if workspace:
        _execute_workspace(workspace, workitem_id, dry_run, stream=stream)
        return

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

    try:
        wt_path = create_worktree(paths, wid, source_branch=config.source_branch)
    except RuntimeError as exc:
        err_console.print(f"[red]Could not create worktree:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    engine = Engine(paths, flow, provider_for=provider_for, execution_cwd=wt_path)

    mode = "[yellow]dry-run[/yellow]" if dry_run else "providers from repo.yml"
    console.print(
        f"Executing [bold]{wid}[/bold] · flow [cyan]{flow.name}[/cyan] · {mode}"
    )
    branch_from = f" · from [bold]{config.source_branch}[/bold]" if config.source_branch else ""
    console.print(
        f"  [dim]worktree: .ai/worktrees/{wid} · branch: conductor/{wid}{branch_from}[/dim]\n"
    )

    spinner = _SpinnerGuard(stream=stream)

    def on_step_start(role: str, provider: str = "") -> None:
        via = f" [dim]({provider})[/dim]" if provider else ""
        spinner.start(f"  [cyan]{role}[/cyan]{via} [dim]thinking...[/dim]")
        if stream:
            console.print(f"  [dim]↓ {role}{' · ' + provider if provider else ''}[/dim]")

    def on_step_output(line: str) -> None:
        console.print(line, end="", markup=False, highlight=False)

    def on_step(step: StepOutcome) -> None:
        elapsed = spinner.stop()
        if stream:
            console.print()  # end of streamed output — move to fresh line
        mark = "[green]✓[/green]" if step.ok else "[red]✗[/red]"
        rel = step.output_path.relative_to(wi.directory).as_posix()
        extra = ""
        if step.verdict and step.verdict != "unknown":
            color = "green" if step.verdict == "approved" else "yellow"
            extra = f" [{color}]{step.verdict}[/{color}]"
            if step.looped_back:
                extra += " [dim]↩ fixing[/dim]"
        console.print(
            f"  {mark} {step.role} [dim]({step.stage} · {step.provider} · {elapsed:.0f}s)[/dim] → {rel}{extra}"
        )

        if step.role == "planner" and step.ok:
            output = step.output_path.read_text(encoding="utf-8")
            m = re.search(r"^BRANCH:\s*(\S+)", output, re.MULTILINE)
            if m:
                console.print(f"\n  [dim]Feature branch:[/dim] [bold]{m.group(1).strip()}[/bold] [dim](created on accept)[/dim]")

    try:
        with spinner:
            outcome = engine.run(
                wid,
                on_step=on_step,
                on_step_start=on_step_start,
                on_step_output=on_step_output if stream else None,
            )
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


def _execute_workspace(
    workspace: str, workitem_id: str | None, dry_run: bool, stream: bool = False
) -> None:
    """Execute a workspace workitem across its target projects."""
    ws_paths = _load_ws_paths(workspace)
    wid = workitem_id or get_active_id(ws_paths)
    if wid is None:
        err_console.print(
            "[red]No workitem to execute.[/red]  "
            f"Run `conductor define -w {workspace} \"<goal>\"` first."
        )
        raise typer.Exit(code=1)

    try:
        wi = load_workitem(ws_paths, wid)
    except FileNotFoundError:
        err_console.print(f"[red]Workitem not found:[/red] {wid}")
        raise typer.Exit(code=1)

    if not wi.goal.target_projects:
        err_console.print(
            "[red]No target_projects set.[/red]  "
            f"Run `conductor refine -w {workspace}` or edit goal.yml to add them."
        )
        raise typer.Exit(code=1)

    try:
        config = load_workspace_config(ws_paths)
    except RepoConfigError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    try:
        ws_flow = load_flow(ws_paths, "workspace-change")
    except FlowNotFound:
        err_console.print(
            "[red]Flow 'workspace-change' not found.[/red]  "
            "Re-run `conductor workspace add` to scaffold the workspace."
        )
        raise typer.Exit(code=1)

    provider_for = build_provider_for(config, dry_run=dry_run)
    engine = WorkspaceEngine(
        ws_paths,
        provider_for,
        max_fix_iterations=ws_flow.max_fix_iterations,
        source_branch=config.source_branch,
    )

    mode = "[yellow]dry-run[/yellow]" if dry_run else "providers from workspace config"
    projects_str = ", ".join(wi.goal.target_projects)
    console.print(
        f"Executing [bold]{wid}[/bold] · workspace [cyan]{workspace}[/cyan] · {mode}"
    )
    console.print(f"  projects: [dim]{projects_str}[/dim]\n")

    spinner = _SpinnerGuard(stream=stream)

    def on_planner_start(provider: str = "") -> None:
        via = f" · {provider}" if provider else ""
        spinner.start(f"  [cyan]planner[/cyan] [dim](workspace-level{via})[/dim]")
        if stream:
            console.print(f"  [dim]↓ planner{' · ' + provider if provider else ''}[/dim]")

    def on_planner(ok: bool, error: str, provider: str = "") -> None:
        elapsed = spinner.stop()
        if stream:
            console.print()
        if ok:
            via = f" · {provider}" if provider else ""
            console.print(f"  [green]✓[/green] planner [dim](workspace-level{via} · {elapsed:.0f}s)[/dim]")
        else:
            console.print(f"  [red]✗[/red] planner — {error}")

    def on_project_step_start(project_name: str, role: str, provider: str = "") -> None:
        via = f" [dim]({provider})[/dim]" if provider else ""
        spinner.start(f"  [bold]{project_name}[/bold] · [cyan]{role}[/cyan]{via} [dim]thinking...[/dim]")
        if stream:
            console.print(f"  [dim]↓ {project_name} · {role}{' · ' + provider if provider else ''}[/dim]")

    def on_project_step(step: ProjectStepOutcome) -> None:
        elapsed = spinner.stop()
        if stream:
            console.print()
        mark = "[green]✓[/green]" if step.ok else "[red]✗[/red]"
        extra = ""
        if step.verdict and step.verdict != "unknown":
            color = "green" if step.verdict == "approved" else "yellow"
            extra = f" [{color}]{step.verdict}[/{color}]"
            if step.looped_back:
                extra += " [dim]↩ fixing[/dim]"
        via = f" · {step.provider}" if step.provider else ""
        console.print(
            f"  {mark} [bold]{step.project_name}[/bold] · {step.role} [dim]({elapsed:.0f}s{via})[/dim]{extra}"
        )

    def on_output(line: str) -> None:
        console.print(line, end="", markup=False, highlight=False)

    try:
        with spinner:
            outcome = engine.run(
                wid,
                on_planner=on_planner,
                on_planner_start=on_planner_start,
                on_project_step=on_project_step,
                on_project_step_start=on_project_step_start,
                on_planner_output=on_output if stream else None,
                on_project_output=on_output if stream else None,
            )
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    except GoalNotApproved as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    except ProviderConfigError as exc:
        err_console.print(f"[red]Provider configuration error:[/red] {exc}")
        raise typer.Exit(code=1)

    if outcome.completed:
        console.print(
            f"\n[green]All projects done.[/green]  "
            f"Review the diffs, then run "
            f"[bold]conductor accept -w {workspace}[/bold]."
        )
    else:
        console.print(f"\n[yellow]Stopped:[/yellow] {outcome.stopped_reason}")
        raise typer.Exit(code=1)


@app.command()
def reopen(
    reason: str = typer.Argument(..., help="Why you're reopening — the planner receives this as directed context."),
    workitem_id: str = typer.Option(
        None, "--id", help="Workitem to reopen (defaults to the active one)."
    ),
    workspace: str = typer.Option(
        None, "--workspace", "-w",
        help="Reopen a cross-project workitem in this workspace."
    ),
    from_role: str = typer.Option(
        None, "--from", help="Restart from this role's step (single-repo only)."
    ),
) -> None:
    """Reopen a completed or blocked workitem with a directed reason.

    Resets the execution state and writes a ``reopen.md`` so the planner
    treats the rerun as a directed revision of the prior plan, not a fresh
    start. Use ``--from <role>`` to restart from a specific step instead of
    the beginning of the flow (single-repo only).
    """
    if workspace:
        _reopen_workspace(workspace, workitem_id, reason)
        return

    paths = _load_paths()
    wid = workitem_id or get_active_id(paths)
    if wid is None:
        err_console.print(
            "[red]No workitem to reopen.[/red]  Run `conductor status --all` to list workitems."
        )
        raise typer.Exit(code=1)

    try:
        wi = load_workitem(paths, wid)
    except FileNotFoundError:
        err_console.print(f"[red]Workitem not found:[/red] {wid}")
        raise typer.Exit(code=1)

    step_index = 0
    if from_role:
        try:
            flow = load_flow(paths, wi.state.flow)
        except FlowNotFound as exc:
            err_console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1)
        idx = flow.index_of_role(from_role)
        if idx is None:
            available = ", ".join(s.role for s in flow.steps)
            err_console.print(
                f"[red]Role '{from_role}' not in flow '{wi.state.flow}'.[/red]  "
                f"Available: {available}"
            )
            raise typer.Exit(code=1)
        step_index = idx

    wt_path = worktree_path(paths, wid)
    if wt_path.is_dir():
        if step_index == 0:
            # Restarting from scratch — discard worktree work but keep feature branch.
            remove_worktree(paths, wid, delete_branch=True)
            console.print(
                f"  [dim]worktree removed · branch conductor/{wid} deleted[/dim]"
            )
        else:
            # Restarting from a later step (e.g. --from reviewer) — the
            # implementer's work in the worktree is still valid, keep it.
            console.print(
                f"  [dim]worktree preserved at .ai/worktrees/{wid}[/dim]"
            )

    updated = reopen_workitem(paths, wid, reason, step_index=step_index)
    console.print(f"[green]Reopened[/green] [bold]{updated.workitem_id}[/bold]")
    console.print(f"  reason: {reason}")
    if from_role:
        console.print(f"  restart from: {from_role} (step index {step_index})")
    console.print(
        f"  state: stage=[cyan]{updated.state.stage}[/cyan] "
        f"status=[yellow]{updated.state.status}[/yellow] "
        f"next=[bold]{updated.state.next_action}[/bold]"
    )
    console.print("\nNext: [bold]conductor execute[/bold]")


def _reopen_workspace(workspace: str, workitem_id: str | None, reason: str) -> None:
    """Reopen a workspace workitem, cleaning up per-project worktrees."""
    ws_paths = _load_ws_paths(workspace)
    wid = workitem_id or get_active_id(ws_paths)
    if wid is None:
        err_console.print(
            "[red]No workitem to reopen.[/red]  "
            f"Run `conductor status -w {workspace} --all` to list workitems."
        )
        raise typer.Exit(code=1)

    try:
        wi = load_workitem(ws_paths, wid)
    except FileNotFoundError:
        err_console.print(f"[red]Workitem not found:[/red] {wid}")
        raise typer.Exit(code=1)

    feature_branch = wi.state.feature_branch
    # Remove each target project's worktree (changes not yet committed are lost).
    for project_name in wi.goal.target_projects:
        project_root = next(
            (p for p in ws_paths.project_roots if p.name == project_name), None
        )
        if project_root is None:
            continue
        project_paths = AiPaths(root=project_root / ".ai")
        wt_path = worktree_path(project_paths, wid)
        if wt_path.is_dir():
            remove_worktree(project_paths, wid, delete_branch=True)
            console.print(
                f"  [dim]{project_name}: worktree removed · branch conductor/{wid} deleted[/dim]"
            )

    updated = reopen_workitem(ws_paths, wid, reason)
    console.print(f"[green]Reopened[/green] [bold]{updated.workitem_id}[/bold]")
    console.print(f"  reason: {reason}")
    console.print(
        f"  state: stage=[cyan]{updated.state.stage}[/cyan] "
        f"status=[yellow]{updated.state.status}[/yellow] "
        f"next=[bold]{updated.state.next_action}[/bold]"
    )
    console.print(f"\nNext: [bold]conductor execute -w {workspace}[/bold]")


@app.command()
def accept(
    workitem_id: str = typer.Option(
        None, "--id", help="Workitem to accept (defaults to the active one)."
    ),
    workspace: str = typer.Option(
        None, "--workspace", "-w",
        help="Accept a cross-project workspace workitem (commits + merges all target projects)."
    ),
    push: bool = typer.Option(
        False, "--push", help="Push each project branch after merging."
    ),
    message: str = typer.Option(
        None, "--message", "-m", help="Override the commit message (default: derived from goal title)."
    ),
) -> None:
    """Commit the working tree changes for the active workitem.

    With ``-w <workspace>``, commits and merges the worktree for every project
    that was part of the workspace execute. Without ``-w``, commits the single-repo
    worktree as usual.

    You remain in control: review the diffs before running this.
    """
    if workspace:
        _accept_workspace(workspace, workitem_id, message, push)
        return

    paths = _load_paths()
    wid = workitem_id or get_active_id(paths)
    if wid is None:
        err_console.print(
            "[red]No active workitem.[/red]  Pass --id or run `conductor status --all`."
        )
        raise typer.Exit(code=1)

    try:
        wi = load_workitem(paths, wid)
    except FileNotFoundError:
        err_console.print(f"[red]Workitem not found:[/red] {wid}")
        raise typer.Exit(code=1)

    try:
        config = load_repo_config(paths)
    except RepoConfigError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    feature_branch = wi.state.feature_branch
    commit_msg = message or _build_commit_msg(wi.state.title, feature_branch, wid)
    merge_msg = (
        f"Merge {feature_branch} into {config.target_branch or 'HEAD'}"
        if feature_branch
        else commit_msg
    )
    repo_root = paths.root.parent
    wt_path = worktree_path(paths, wid)

    if wt_path.is_dir():
        # Worktree path: commit in the isolated branch, then merge into target.
        try:
            committed = commit_worktree(paths, wid, commit_msg)
        except RuntimeError as exc:
            err_console.print(f"[red]git commit in worktree failed:[/red] {exc}")
            raise typer.Exit(code=1) from exc

        if not committed:
            console.print("[yellow]Nothing to commit[/yellow] — worktree is clean.")
            raise typer.Exit(code=0)

        try:
            merge_worktree(paths, wid, merge_msg, target_branch=config.target_branch)
        except RuntimeError as exc:
            err_console.print(f"[red]{exc}[/red]")
            err_console.print(
                "[dim]Resolve conflicts manually, then run `git merge --continue`.[/dim]"
            )
            raise typer.Exit(code=1) from exc

        # Create feature branch at the committed tip (before worktree dir is removed).
        if feature_branch:
            subprocess.run(
                ["git", "branch", "-f", feature_branch, worktree_branch(wid)],
                cwd=repo_root, capture_output=True, text=True,
            )

        remove_worktree(paths, wid)
        console.print(f"[green]Accepted[/green] [bold]{wid}[/bold]")
        console.print(f"  commit: {commit_msg.splitlines()[0]}")
        merged_into = config.target_branch or "current branch"
        if feature_branch:
            console.print(f"  [dim]{feature_branch} → {merged_into} · worktree removed[/dim]")
            console.print(f"  [dim]PR ready: [bold]{feature_branch}[/bold] → main[/dim]")
        else:
            console.print(
                f"  [dim]branch conductor/{wid} merged into {merged_into} · worktree removed[/dim]"
            )
    else:
        # Legacy path: no worktree, commit directly in the working tree.
        stage = subprocess.run(
            ["git", "add", "-A"], cwd=repo_root, capture_output=True, text=True
        )
        if stage.returncode != 0:
            err_console.print(f"[red]git add failed:[/red] {stage.stderr.strip()}")
            raise typer.Exit(code=1)

        diff_check = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=repo_root
        )
        if diff_check.returncode == 0:
            console.print("[yellow]Nothing to commit[/yellow] — working tree is clean.")
            raise typer.Exit(code=0)

        commit = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=repo_root, capture_output=True, text=True,
        )
        if commit.returncode != 0:
            err_console.print(f"[red]git commit failed:[/red] {commit.stderr.strip()}")
            raise typer.Exit(code=1)

        console.print(f"[green]Committed[/green] [bold]{wid}[/bold]")
        console.print(f"  message: {commit_msg}")

    if push:
        push_result = subprocess.run(
            ["git", "push"], cwd=repo_root, capture_output=True, text=True
        )
        if push_result.returncode != 0:
            err_console.print(f"[red]git push failed:[/red] {push_result.stderr.strip()}")
            raise typer.Exit(code=1)
        console.print("[green]Pushed.[/green]")
        if feature_branch:
            fp_result = subprocess.run(
                ["git", "push", "-u", "origin", feature_branch],
                cwd=repo_root, capture_output=True, text=True,
            )
            if fp_result.returncode == 0:
                console.print(f"[green]Pushed[/green] {feature_branch}")
            else:
                console.print(f"  [yellow]![/yellow] could not push {feature_branch}: {fp_result.stderr.strip()}")


# ---------------------------------------------------------------------------
# Known provider CLIs and their typical defaults
# ---------------------------------------------------------------------------
_CLI_DEFAULTS: dict[str, dict] = {
    "claude":    {"type": "cli_one_shot", "command": "claude", "args": ["-p"], "prompt_via": "arg", "timeout": 3600},
    "opencode":  {"type": "cli_one_shot", "command": "opencode", "args": ["run"], "prompt_via": "arg", "timeout": 3600},
    "qwen":      {"type": "cli_one_shot", "command": "qwen", "args": ["--approval-mode", "yolo"], "prompt_via": "arg", "timeout": 3600},
    "codex":     {"type": "cli_one_shot", "command": "codex", "args": [], "prompt_via": "stdin", "timeout": 3600},
}
_KNOWN_ROLES = ("refiner", "planner", "implementer", "reviewer", "validator")


def _detect_available_clis() -> list[str]:
    return [name for name in _CLI_DEFAULTS if shutil.which(name)]


def _prompt_provider_name(role: str, current: str | None, available: list[str]) -> str | None:
    """Ask which CLI to use for a role. Returns provider key (CLI name) or None to skip."""
    choices = available + ["skip"]
    current_label = f" [dim](current: {current})[/dim]" if current else ""
    console.print(f"\n  [bold]{role}[/bold]{current_label}")
    for i, c in enumerate(choices, 1):
        mark = " ← current" if c == current else ""
        console.print(f"    {i}. {c}{mark}")
    raw = typer.prompt(f"  Choose (1-{len(choices)})", default="1")
    try:
        idx = int(raw) - 1
        chosen = choices[idx]
    except (ValueError, IndexError):
        chosen = choices[0]
    return None if chosen == "skip" else chosen


def _write_repo_yml(paths: AiPaths, config) -> None:
    """Overwrite repo.yml with the current config (preserving name + flow)."""
    import yaml as _yaml
    existing_raw = {}
    if paths.repo_config.is_file():
        existing_raw = _yaml.safe_load(paths.repo_config.read_text(encoding="utf-8")) or {}

    data = {
        "name": existing_raw.get("name", paths.cwd.name),
        "default_flow": existing_raw.get("default_flow", "simple-change"),
        "providers": {n: _provider_to_dict(p) for n, p in config.providers.items()},
        "roles": {r: {"provider": rb.provider} for r, rb in config.roles.items()},
    }
    if "refine" in existing_raw:
        data["refine"] = existing_raw["refine"]

    paths.repo_config.write_text(
        "# workitem-conductor repo configuration\n"
        + _yaml.dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )


@app.command(name="config")
def config_cmd(
    global_: bool = typer.Option(
        False, "--global", "-g",
        help="Configure global defaults (~/.config/conductor/defaults.yml) instead of this repo."
    ),
) -> None:
    """Interactively configure provider/role bindings for this repo (or globally).

    Walks through each role and lets you assign a CLI provider. Global defaults
    (``--global``) are inherited by every repo that doesn't override them locally.
    """
    available = _detect_available_clis()
    if not available:
        err_console.print(
            "[red]No known provider CLIs found on PATH.[/red]  "
            "Install at least one of: " + ", ".join(_CLI_DEFAULTS)
        )
        raise typer.Exit(code=1)

    if global_:
        _config_global(available)
    else:
        _config_repo(available)


def _config_global(available: list[str]) -> None:
    """Wizard for global defaults."""
    current = load_global_defaults()

    console.print(
        f"\n[bold]Global defaults[/bold]  "
        f"[dim](~/.config/conductor/defaults.yml)[/dim]\n"
        f"Inherited by every repo unless overridden locally.\n"
    )
    console.print(f"Available CLIs: " + "  ".join(f"[green]{c}[/green]" for c in available))

    new_providers: dict[str, ProviderConfig] = {}
    new_roles: dict[str, RoleBinding] = {}

    for role in _KNOWN_ROLES:
        current_provider = current.roles.get(role)
        current_cli = current_provider.provider if current_provider else None
        chosen = _prompt_provider_name(role, current_cli, available)
        if chosen is None:
            if current_provider:
                new_roles[role] = current_provider
                if current_cli and current_cli in current.providers:
                    new_providers[current_cli] = current.providers[current_cli]
            continue
        defaults = _CLI_DEFAULTS[chosen]
        new_providers[chosen] = ProviderConfig.model_validate(defaults)
        new_roles[role] = RoleBinding(provider=chosen)

    if not new_roles:
        console.print("\n[yellow]No roles configured — nothing written.[/yellow]")
        return

    save_global_defaults(RepoConfig(providers=new_providers, roles=new_roles))
    console.print(
        f"\n[green]Global defaults saved.[/green]  "
        f"[dim]~/.config/conductor/defaults.yml[/dim]"
    )
    console.print("New repos will inherit these settings automatically.")


def _config_repo(available: list[str]) -> None:
    """Wizard for the current repo's .ai/repo.yml."""
    paths = _load_paths()
    # Load merged config (defaults + local) so we show the effective current state
    try:
        effective = load_repo_config(paths)
    except RepoConfigError:
        effective = load_global_defaults()

    console.print(
        f"\n[bold]Repo config[/bold]  [dim]({paths.repo_config})[/dim]\n"
        "Local settings override global defaults for this repo only.\n"
    )
    console.print(f"Available CLIs: " + "  ".join(f"[green]{c}[/green]" for c in available))

    new_providers: dict[str, ProviderConfig] = {}
    new_roles: dict[str, RoleBinding] = {}

    for role in _KNOWN_ROLES:
        current_provider = effective.roles.get(role)
        current_cli = current_provider.provider if current_provider else None
        chosen = _prompt_provider_name(role, current_cli, available)
        if chosen is None:
            continue
        defaults = _CLI_DEFAULTS[chosen]
        new_providers[chosen] = ProviderConfig.model_validate(defaults)
        new_roles[role] = RoleBinding(provider=chosen)

    if not new_roles:
        console.print("\n[yellow]No roles configured — repo.yml unchanged.[/yellow]")
        return

    _write_repo_yml(paths, RepoConfig(providers=new_providers, roles=new_roles))
    console.print(
        f"\n[green]Saved.[/green]  [dim]{paths.repo_config}[/dim]\n"
        "Run [bold]conductor doctor[/bold] to verify bindings."
    )


def _accept_workspace(
    workspace: str, workitem_id: str | None, message: str | None, push: bool
) -> None:
    """Commit + merge worktrees for all target projects of a workspace workitem."""
    ws_paths = _load_ws_paths(workspace)
    wid = workitem_id or get_active_id(ws_paths)
    if wid is None:
        err_console.print(
            "[red]No active workspace workitem.[/red]  Pass --id or run `conductor status -w`."
        )
        raise typer.Exit(code=1)

    try:
        wi = load_workitem(ws_paths, wid)
    except FileNotFoundError:
        err_console.print(f"[red]Workitem not found:[/red] {wid}")
        raise typer.Exit(code=1)

    try:
        config = load_workspace_config(ws_paths)
    except RepoConfigError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    feature_branch = wi.state.feature_branch
    commit_msg = message or _build_commit_msg(wi.state.title, feature_branch, wid)
    merge_msg = (
        f"Merge {feature_branch} into {config.target_branch or 'HEAD'}"
        if feature_branch
        else commit_msg
    )
    any_accepted = False

    for project_name in wi.goal.target_projects:
        project_root = next(
            (p for p in ws_paths.project_roots if p.name == project_name), None
        )
        if project_root is None:
            console.print(f"  [yellow]![/yellow] {project_name}: not found in workspace, skipping")
            continue

        project_paths = AiPaths(root=project_root / ".ai")
        wt_path = worktree_path(project_paths, wid)

        if not wt_path.is_dir():
            console.print(f"  [dim]= {project_name}: no worktree, skipping[/dim]")
            continue

        try:
            committed = commit_worktree(project_paths, wid, commit_msg)
        except RuntimeError as exc:
            err_console.print(f"  [red]✗[/red] {project_name}: commit failed — {exc}")
            raise typer.Exit(code=1) from exc

        if not committed:
            console.print(f"  [dim]= {project_name}: nothing to commit[/dim]")
            remove_worktree(project_paths, wid)
            continue

        try:
            merge_worktree(project_paths, wid, merge_msg, target_branch=config.target_branch)
        except RuntimeError as exc:
            err_console.print(f"  [red]✗[/red] {project_name}: merge failed — {exc}")
            err_console.print("[dim]Resolve conflicts manually, then run `git merge --continue`.[/dim]")
            raise typer.Exit(code=1) from exc

        if feature_branch:
            subprocess.run(
                ["git", "branch", "-f", feature_branch, worktree_branch(wid)],
                cwd=project_root, capture_output=True, text=True,
            )

        remove_worktree(project_paths, wid)
        merged_into = config.target_branch or "current branch"
        console.print(f"  [green]✓[/green] {project_name}: merged into {merged_into} · worktree removed")
        any_accepted = True

        if push:
            push_result = subprocess.run(
                ["git", "push"], cwd=project_root, capture_output=True, text=True
            )
            if push_result.returncode != 0:
                err_console.print(
                    f"  [red]✗[/red] {project_name}: push failed — {push_result.stderr.strip()}"
                )
                raise typer.Exit(code=1)
            console.print(f"  [green]✓[/green] {project_name}: pushed")
            if feature_branch:
                fp_result = subprocess.run(
                    ["git", "push", "-u", "origin", feature_branch],
                    cwd=project_root, capture_output=True, text=True,
                )
                if fp_result.returncode == 0:
                    console.print(f"  [green]✓[/green] {project_name}: pushed {feature_branch}")

    if any_accepted:
        console.print(f"\n[green]Accepted[/green] [bold]{wid}[/bold]")
        console.print(f"  commit: {commit_msg.splitlines()[0]}")
        if feature_branch:
            console.print(f"  [dim]PR ready: [bold]{feature_branch}[/bold] → main[/dim]")
    else:
        console.print(f"[yellow]Nothing to accept[/yellow] for {wid} — all worktrees were clean.")


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
