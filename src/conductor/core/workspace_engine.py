"""Two-phase engine for workspace (cross-project) workitem execution.

Phase 1 — planner (workspace-level):
  Runs once with cross-project context. Produces a per-project plan.

Phase 2 — implementer + reviewer (per project):
  For each project in ``target_projects``, creates an isolated worktree and
  runs the implement → review loop. The workspace planner output is injected
  into every project's implementer context.

Run/metrics recording and the ``summarizer`` role are the single-repo
``Engine``'s fast-followers here: one ``run.yml``/``metrics.yml`` per
workspace run (not per project — ``StepRecord.project_name`` distinguishes
the planner step from each project's steps), and the summarizer is called
once at the end rather than per-project. A ``STOP:`` safety marker from any
role halts the *entire* workspace run immediately (fail-fast), unlike a
plain provider failure which only skips that one project.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..paths import AiPaths, WorkspacePaths
from ..providers.base import Provider, ProviderRequest
from ..workitems.manager import Workitem, load_workitem, save_state
from ..workitems.models import StopReason, utcnow_iso
from . import stop_conditions
from .context import build_workspace_planner_context, build_workspace_project_context
from .review import parse_review_verdict
from .runs import MetricsRecord, RunRecord, StepRecord, next_run_id, write_run
from .stop_conditions import parse_stop_signal
from .summarize import summarize
from .worktree import create_worktree

ProviderFor = Callable[[str], Provider]


@dataclass
class ProjectStepOutcome:
    project_name: str
    role: str
    ok: bool
    output_path: Path | None = None
    verdict: str | None = None
    looped_back: bool = False
    error: str | None = None
    provider: str | None = None


@dataclass
class WorkspaceRunOutcome:
    workitem_id: str
    planner_ok: bool = False
    planner_output_path: Path | None = None
    project_steps: list[ProjectStepOutcome] = field(default_factory=list)
    completed: bool = False
    stopped_reason: StopReason | None = None


class WorkspaceEngine:
    def __init__(
        self,
        ws_paths: WorkspacePaths,
        provider_for: ProviderFor,
        max_fix_iterations: int = 3,
        source_branch: str | None = None,
        flow_name: str = "workspace-change",
    ) -> None:
        self.ws_paths = ws_paths
        self.provider_for = provider_for
        self.max_fix_iterations = max_fix_iterations
        self.source_branch = source_branch
        self.flow_name = flow_name

    def run(
        self,
        workitem_id: str,
        on_planner: Callable[[bool, str], None] | None = None,
        on_project_step: Callable[[ProjectStepOutcome], None] | None = None,
        on_planner_start: Callable[[str], None] | None = None,
        on_project_step_start: Callable[[str, str, str], None] | None = None,
        on_planner_output: Callable[[str], None] | None = None,
        on_project_output: Callable[[str], None] | None = None,
    ) -> WorkspaceRunOutcome:
        wi = load_workitem(self.ws_paths, workitem_id)
        target_projects = wi.goal.target_projects

        if not wi.goal.approved:
            from .engine import GoalNotApproved
            raise GoalNotApproved(workitem_id)

        if not target_projects:
            raise ValueError(
                f"Workitem '{workitem_id}' has no target_projects set. "
                "Run `conductor refine -w <workspace>` or edit goal.yml to add them."
            )

        state = wi.state
        outputs_dir = wi.directory / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        outcome = WorkspaceRunOutcome(workitem_id=workitem_id)

        run_id = next_run_id(wi.directory)
        started_at = utcnow_iso()
        run_steps: list[StepRecord] = []

        state.status = "running"
        state.stage = "planning"
        save_state(self.ws_paths, state)

        # ------------------------------------------------------------------
        # Phase 1: planner (workspace-level, runs once)
        # ------------------------------------------------------------------
        planner_prompt = build_workspace_planner_context(self.ws_paths, wi)
        planner_run = sum(1 for _ in outputs_dir.glob("??-planner.output.md"))
        planner_prompt_path = outputs_dir / f"{planner_run:02d}-planner.prompt.md"
        planner_output_path = outputs_dir / f"{planner_run:02d}-planner.output.md"
        planner_prompt_path.write_text(planner_prompt, encoding="utf-8")
        planner_provider = self.provider_for("planner")
        if on_planner_start:
            on_planner_start(planner_provider.name)
        t0 = time.monotonic()
        planner_result = planner_provider.run(
            ProviderRequest(
                role="planner",
                prompt=planner_prompt,
                workitem_id=workitem_id,
                cwd=self.ws_paths.cwd,
                on_output=on_planner_output,
            )
        )
        planner_duration = time.monotonic() - t0
        planner_output_path.write_text(planner_result.output or "", encoding="utf-8")
        run_steps.append(
            StepRecord(
                index=0,
                role="planner",
                provider=planner_result.provider,
                ok=planner_result.ok,
                duration_sec=round(planner_duration, 2),
                prompt_chars=len(planner_prompt),
                output_chars=len(planner_result.output or ""),
                prompt_path=planner_prompt_path.relative_to(wi.directory).as_posix(),
                output_path=planner_output_path.relative_to(wi.directory).as_posix(),
                project_name=None,
            )
        )

        if on_planner:
            on_planner(planner_result.ok, planner_result.error or "", planner_result.provider)

        if not planner_result.ok:
            state.status = "blocked"
            state.stage = "blocked"
            state.next_action = "none"
            state.record(f"planner failed: {planner_result.error or 'unknown'}")
            save_state(self.ws_paths, state)
            outcome.stopped_reason = StopReason(
                type="provider_error",
                message=f"planner failed: {planner_result.error or 'unknown'}",
            )
            self._write_run(wi, run_id, started_at, run_steps, outcome)
            self._summarize(wi, "stop", run_steps)
            return outcome

        stop_signal = parse_stop_signal(planner_result.output)
        if stop_signal is not None:
            self._stop_project(state, None, stop_signal, outcome)
            self._write_run(wi, run_id, started_at, run_steps, outcome)
            self._summarize(wi, "stop", run_steps)
            return outcome

        outcome.planner_ok = True
        outcome.planner_output_path = planner_output_path
        m = re.search(r"^BRANCH:\s*(\S+)", planner_result.output or "", re.MULTILINE)
        if m:
            state.feature_branch = m.group(1).strip()
        state.record(f"planner completed via {planner_result.provider}")
        save_state(self.ws_paths, state)

        workspace_plan = planner_result.output or ""

        # ------------------------------------------------------------------
        # Phase 2: implementer + reviewer, once per target project
        # ------------------------------------------------------------------
        all_ok = True
        stopped_all = False
        for project_name in target_projects:
            project_root = self._resolve_project(project_name)
            if project_root is None:
                msg = f"project '{project_name}' not found in workspace registry"
                state.open_issues.append(msg)
                state.record(f"skipped {project_name}: {msg}")
                save_state(self.ws_paths, state)
                all_ok = False
                continue

            project_paths = AiPaths(root=project_root / ".ai")
            project_outputs = outputs_dir / project_name
            project_outputs.mkdir(parents=True, exist_ok=True)

            try:
                wt_path = create_worktree(
                    project_paths, workitem_id, source_branch=self.source_branch
                )
            except RuntimeError as exc:
                msg = f"worktree for '{project_name}' failed: {exc}"
                state.open_issues.append(msg)
                state.record(msg)
                save_state(self.ws_paths, state)
                all_ok = False
                continue

            state.stage = "implementing"
            save_state(self.ws_paths, state)

            result = self._run_project(
                wi=wi,
                project_name=project_name,
                project_paths=project_paths,
                project_outputs=project_outputs,
                wt_path=wt_path,
                workspace_plan=workspace_plan,
                state=state,
                outcome=outcome,
                run_steps=run_steps,
                on_step=on_project_step,
                on_step_start=on_project_step_start,
                on_output=on_project_output,
            )
            if result == "stop_all":
                stopped_all = True
                all_ok = False
                break
            if not result:
                all_ok = False

        if stopped_all:
            # state.status/stage/stop_reason were already set inside
            # _run_project's stop path (via _stop_project); outcome.stopped_reason
            # too — nothing more to compute here, just fall through to recording.
            pass
        elif all_ok:
            outcome.completed = True
            state.stage = "completed"
            state.status = "completed"
            state.next_action = "none"
            state.record("all projects implemented and reviewed")
            save_state(self.ws_paths, state)
        else:
            state.stage = "blocked"
            state.status = "blocked"
            state.next_action = "none"
            outcome.stopped_reason = StopReason(
                type="project_failed",
                message="one or more projects failed — see open_issues",
            )
            save_state(self.ws_paths, state)

        self._write_run(wi, run_id, started_at, run_steps, outcome)
        self._summarize(wi, "finish" if outcome.completed else "stop", run_steps)
        return outcome

    def _resolve_project(self, name: str) -> Path | None:
        for root in self.ws_paths.project_roots:
            if root.name == name:
                return root
        return None

    def _stop_project(
        self,
        state,
        project_name: str | None,
        stop_reason: StopReason,
        outcome: WorkspaceRunOutcome,
    ) -> None:
        """Record a safety stop raised by the planner or one project's role.

        ``project_name`` is ``None`` for a planner-raised stop (workspace-level,
        before any project has started) and the project name for an
        implementer/reviewer-raised stop. Unlike a per-project provider
        failure (which is recorded and the loop moves on to the next
        project), a ``STOP:`` marker halts the entire workspace run — the
        caller (``run()``) returns or breaks out of the project loop as soon
        as this returns.
        """
        state.status = stop_conditions.status_for(stop_reason.type)
        state.stage = "blocked"
        state.next_action = "none"
        state.stop_reason = stop_reason
        prefix = f"{project_name}: " if project_name else ""
        state.record(f"{prefix}stopped ({stop_reason.type}): {stop_reason.message}")
        save_state(self.ws_paths, state)
        outcome.stopped_reason = stop_reason

    def _run_project(
        self,
        *,
        wi: Workitem,
        project_name: str,
        project_paths: AiPaths,
        project_outputs: Path,
        wt_path: Path,
        workspace_plan: str,
        state,
        outcome: WorkspaceRunOutcome,
        run_steps: list[StepRecord],
        on_step: Callable[[ProjectStepOutcome], None] | None,
        on_step_start: Callable[[str, str, str], None] | None = None,
        on_output: Callable[[str], None] | None = None,
    ) -> bool | str:
        """Run the implement → review loop for one project.

        Returns ``True`` if approved, ``False`` if failed (skip-and-continue
        with other projects — the existing behavior for a technical/provider
        problem), or ``"stop_all"`` if a role raised a ``STOP:`` safety
        marker, which the caller must treat as halting the entire workspace
        run rather than just this project.
        """
        fix_iteration = 0
        seq = sum(1 for _ in project_outputs.glob("??-*.output.md")) + 1
        prior_impl_output: str | None = None

        while True:
            cap = stop_conditions.check_global_cap(seq)
            if cap.stop:
                state.open_issues.append(f"{project_name}: {cap.reason}")
                state.record(f"{project_name}: stopped — {cap.reason}")
                save_state(self.ws_paths, state)
                return False

            # Implementer
            impl_seq = seq
            impl_prompt = build_workspace_project_context(
                project_paths,
                wi,
                "implementer",
                workspace_plan=workspace_plan,
                prior_implementer_output=prior_impl_output,
                fix_iteration=fix_iteration,
            )
            impl_prompt_path = project_outputs / f"{impl_seq:02d}-implementer.prompt.md"
            impl_path = project_outputs / f"{impl_seq:02d}-implementer.output.md"
            impl_prompt_path.write_text(impl_prompt, encoding="utf-8")
            impl_provider = self.provider_for("implementer")
            if on_step_start:
                on_step_start(project_name, "implementer", impl_provider.name)
            t0 = time.monotonic()
            impl_result = impl_provider.run(
                ProviderRequest(
                    role="implementer",
                    prompt=impl_prompt,
                    workitem_id=wi.workitem_id,
                    cwd=wt_path,
                    on_output=on_output,
                )
            )
            impl_duration = time.monotonic() - t0
            impl_path.write_text(impl_result.output or "", encoding="utf-8")

            impl_outcome = ProjectStepOutcome(
                project_name=project_name,
                role="implementer",
                ok=impl_result.ok,
                output_path=impl_path,
                error=impl_result.error,
                provider=impl_result.provider,
            )
            outcome.project_steps.append(impl_outcome)
            run_steps.append(
                StepRecord(
                    index=impl_seq,
                    role="implementer",
                    provider=impl_result.provider,
                    ok=impl_result.ok,
                    duration_sec=round(impl_duration, 2),
                    prompt_chars=len(impl_prompt),
                    output_chars=len(impl_result.output or ""),
                    prompt_path=impl_prompt_path.relative_to(wi.directory).as_posix(),
                    output_path=impl_path.relative_to(wi.directory).as_posix(),
                    project_name=project_name,
                )
            )
            if on_step:
                on_step(impl_outcome)

            if not impl_result.ok:
                state.open_issues.append(
                    f"{project_name}: implementer failed — {impl_result.error or 'unknown'}"
                )
                state.record(f"{project_name}: implementer failed")
                save_state(self.ws_paths, state)
                return False

            stop_signal = parse_stop_signal(impl_result.output)
            if stop_signal is not None:
                self._stop_project(state, project_name, stop_signal, outcome)
                return "stop_all"

            prior_impl_output = impl_result.output
            seq += 1

            # Reviewer
            rev_seq = seq
            rev_prompt = build_workspace_project_context(
                project_paths,
                wi,
                "reviewer",
                workspace_plan=workspace_plan,
                prior_implementer_output=prior_impl_output,
                fix_iteration=fix_iteration,
            )
            rev_prompt_path = project_outputs / f"{rev_seq:02d}-reviewer.prompt.md"
            rev_path = project_outputs / f"{rev_seq:02d}-reviewer.output.md"
            rev_prompt_path.write_text(rev_prompt, encoding="utf-8")
            rev_provider = self.provider_for("reviewer")
            if on_step_start:
                on_step_start(project_name, "reviewer", rev_provider.name)
            t0 = time.monotonic()
            rev_result = rev_provider.run(
                ProviderRequest(
                    role="reviewer",
                    prompt=rev_prompt,
                    workitem_id=wi.workitem_id,
                    cwd=wt_path,
                    on_output=on_output,
                )
            )
            rev_duration = time.monotonic() - t0
            rev_path.write_text(rev_result.output or "", encoding="utf-8")
            seq += 1

            stop_signal = parse_stop_signal(rev_result.output) if rev_result.ok else None
            verdict = "unknown"
            if rev_result.ok and stop_signal is None:
                verdict = parse_review_verdict(rev_result.output)

            rev_outcome = ProjectStepOutcome(
                project_name=project_name,
                role="reviewer",
                ok=rev_result.ok,
                output_path=rev_path,
                verdict=verdict if stop_signal is None else None,
                error=rev_result.error,
                provider=rev_result.provider,
            )
            outcome.project_steps.append(rev_outcome)
            run_steps.append(
                StepRecord(
                    index=rev_seq,
                    role="reviewer",
                    provider=rev_result.provider,
                    ok=rev_result.ok,
                    duration_sec=round(rev_duration, 2),
                    prompt_chars=len(rev_prompt),
                    output_chars=len(rev_result.output or ""),
                    prompt_path=rev_prompt_path.relative_to(wi.directory).as_posix(),
                    output_path=rev_path.relative_to(wi.directory).as_posix(),
                    verdict=rev_outcome.verdict,
                    project_name=project_name,
                )
            )
            if on_step:
                on_step(rev_outcome)

            if not rev_result.ok:
                state.open_issues.append(
                    f"{project_name}: reviewer failed — {rev_result.error or 'unknown'}"
                )
                save_state(self.ws_paths, state)
                return False

            if stop_signal is not None:
                self._stop_project(state, project_name, stop_signal, outcome)
                return "stop_all"

            if verdict == "approved":
                state.record(f"{project_name}: reviewer approved")
                save_state(self.ws_paths, state)
                return True

            if verdict == "changes_requested":
                decision = stop_conditions.check_max_fix_iterations(
                    fix_iteration, self.max_fix_iterations
                )
                if decision.stop:
                    state.open_issues.append(f"{project_name}: {decision.reason}")
                    state.record(f"{project_name}: fix limit reached")
                    save_state(self.ws_paths, state)
                    rev_outcome.looped_back = False
                    return False
                fix_iteration += 1
                rev_outcome.looped_back = True
                run_steps[-1].looped_back = True
                state.record(
                    f"{project_name}: reviewer requested changes — fix {fix_iteration}"
                )
                save_state(self.ws_paths, state)
                continue

            # verdict == "unknown" — treat as approved with a warning
            state.record(f"{project_name}: reviewer gave no clear verdict — treating as approved")
            save_state(self.ws_paths, state)
            return True

    def _write_run(
        self,
        wi: Workitem,
        run_id: str,
        started_at: str,
        run_steps: list[StepRecord],
        outcome: WorkspaceRunOutcome,
    ) -> None:
        """Write this run's manifest and aggregated metrics.

        Mirrors ``Engine._write_run`` but scoped to one workspace run — steps
        span the planner (``project_name=None``) and every project's
        implementer/reviewer steps. No multi-project git-stat aggregation yet
        (``metrics.git`` stays ``None``), same additive-first posture M4 used
        for single-repo before it grew richer.
        """
        state = wi.state
        run = RunRecord(
            run_id=run_id,
            workitem_id=wi.workitem_id,
            started_at=started_at,
            finished_at=utcnow_iso(),
            status=state.status,
            flow=self.flow_name,
            reopen_number=state.reopen_count,
            stop_reason=outcome.stopped_reason,
            steps=run_steps,
        )
        prompt_chars = [s.prompt_chars for s in run_steps]
        providers = {
            (f"{s.project_name}:{s.role}" if s.project_name else s.role): s.provider
            for s in run_steps
        }
        metrics = MetricsRecord(
            context={
                "total_prompt_chars": sum(prompt_chars),
                "max_step_prompt_chars": max(prompt_chars, default=0),
            },
            git=None,
            loop={"reopen_number": state.reopen_count},
            providers=providers,
        )
        write_run(wi.directory, run, metrics)

    def _summarize(self, wi: Workitem, trigger: str, run_steps: list[StepRecord]) -> None:
        """Best-effort call to the ``summarizer`` role, once per workspace run.

        Same never-break-the-run posture as ``Engine._summarize``.
        """
        try:
            provider = self.provider_for("summarizer")
            summarize(self.ws_paths, wi, provider, trigger, run_steps, self.ws_paths.cwd)
        except Exception as exc:  # summarization must never break the run
            wi.state.record(f"summarizer skipped: {exc}")
            save_state(self.ws_paths, wi.state)
