"""LoopCommand: orchestrates the plan → refactor → prover → review → finalize loop.

The Typer-decorated `loop` function lives in :mod:`.entry` so this
module can stay focused on orchestration. `LoopCommand.run()` is the
behavior: read state, start services, iterate phases, summarize.
"""

from __future__ import annotations

import time
from pathlib import Path

import typer

from archon import log
from archon.commands.tooling.version import warn_if_mismatch, warn_if_prompts_drifted
from archon.state import (
    cleanup_empty_sessions,
    cost_summary,
    is_complete,
    next_iter_num,
    read_stage,
    write_stage,
    utcnow_iso,
    write_meta,
)

from . import plan_validate
from .context import LoopContext, LoopOptions
from .phases import (
    FinalizePhase,
    PlanPhase,
    PreCompactPlanPhase,
    PreCompactReviewPhase,
    ProverPhase,
    ReviewPhase,
    SyncLeanokPhase,
)
from .preflight import (
    check_informal_agent_keys,
    preflight,
    warn_if_inner_dirty,
)
from .services import BlueprintServerProcess, DashboardProcess
from .sorry_count import count_sorries
from .utils import describe_finalize


_PHASES = ("plan", "prover", "review")


class LoopCommand:
    """Orchestrates one full `archon loop` invocation."""

    def __init__(self, options: LoopOptions) -> None:
        self.options = options
        self.ctx: LoopContext | None = None
        self.loop_start: float = 0.0

    def run(self) -> None:
        self._bootstrap_context()
        self._announce()
        self._start_services()

        ctx = self.ctx  # bound after _bootstrap_context
        ctx.initial_sorry = count_sorries(ctx.project_path) if not ctx.dry_run else None

        if is_complete(ctx.progress_file, ctx.force_stage()):
            if ctx.initial_sorry is not None and ctx.initial_sorry > 0:
                log.warn(
                    f"Project is marked COMPLETE, but {ctx.initial_sorry} "
                    f"sorries were found."
                )
                write_stage(ctx.progress_file, "prover")
                log.warn(
                    "Stage reset to 'prover' to address the sorries; the loop "
                    "will run to attempt to resolve them."
                )
            else:
                log.success(f"Project '{ctx.project_name}' is COMPLETE. Nothing to do.")
                if ctx.dashboard_url:
                    log.step(f"Review results in the dashboard: {ctx.dashboard_url}")
                return

        if not ctx.dry_run:
            check_informal_agent_keys()

        if ctx.initial_sorry is not None:
            log.info(f"Starting sorry count: {ctx.initial_sorry}")

        if not ctx.dashboard_url:
            log.info(
                f"To visualize progress and logs, run: "
                f"`archon dashboard {self.options.project_path}`"
            )

        self.loop_start = time.monotonic()
        ctx.prev_sorry = ctx.initial_sorry

        for i in range(self.options.max_iterations):
            if not self._run_iteration(i):
                break

        self._summarize()

    # ── bootstrap ──────────────────────────────────────────────────────

    def _bootstrap_context(self) -> None:
        opts = self.options
        resolved = opts.project_path
        state_dir = resolved / ".archon"
        progress_file = state_dir / "PROGRESS.md"
        log_dir = state_dir / "logs"

        preflight(resolved, state_dir, opts.dry_run)

        if not opts.dry_run:
            log_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "task_results").mkdir(exist_ok=True)
            (state_dir / "proof-journal" / "sessions").mkdir(parents=True, exist_ok=True)
            (state_dir / "proof-journal" / "current_session").mkdir(parents=True, exist_ok=True)
            # Sweep stub session_* dirs left behind by interrupted
            # review runs or by `git reset --hard`s that walked back
            # past dirs the inner git doesn't track. Keeps the Journal
            # UI's session list aligned with the iter numbers it
            # actually has data for.
            removed = cleanup_empty_sessions(state_dir)
            if removed:
                log.info(
                    f"Cleaned up {removed} empty session dir(s) in "
                    f"proof-journal/sessions/"
                )

        current_stage = read_stage(progress_file, opts.force_stage)

        self.ctx = LoopContext(
            options=opts,
            project_path=resolved,
            project_name=resolved.name,
            state_dir=state_dir,
            progress_file=progress_file,
            log_dir=log_dir,
            current_stage=current_stage,
        )

    def _announce(self) -> None:
        ctx = self.ctx
        opts = self.options

        prover_mode = "parallel" if opts.parallel else "serial"
        if opts.parallel:
            prover_mode += f" (max {opts.max_parallel})"

        multilane_lanes = opts.multilane_cfg.get('lanes') or []
        config_panel = {
            "Project": str(opts.project_path),
            "Stage": opts.force_stage or ctx.current_stage,
            "Max iterations": str(opts.max_iterations),
            "Prover mode": prover_mode,
            "Review": "enabled" if not opts.no_review else "disabled",
            "Finalize": describe_finalize(opts.do_git, opts.do_lake, opts.do_bp_web),
            "Dashboard": "disabled" if opts.no_dashboard else "enabled",
            "Blueprint server": "enabled" if opts.blueprint_server_flag else "disabled",
            "Logs": str(ctx.log_dir),
            "User hints": str(ctx.state_dir / "USER_HINTS.md"),
            "Debug feedback": "enabled" if opts.debug_feedback else "disabled",
            "Multi-lane": (
                f"enabled ({len(multilane_lanes)} lane{'s' if len(multilane_lanes) != 1 else ''}: "
                f"{', '.join(str(l.get('lane_id', l.get('provider', '?'))) for l in multilane_lanes)})"
                if opts.multilane_execute else "disabled"
            ),
        }
        if opts.dry_run:
            config_panel["Mode"] = "[yellow]DRY RUN[/yellow]"

        log.header("Archon Loop")
        log.key_value(config_panel)
        warn_if_mismatch(opts.project_path)
        warn_if_prompts_drifted(opts.project_path)

        # Warn (but do not block) if the inner git has leftover agent
        # work — the user may have Ctrl-C'd a previous loop mid-phase.
        warn_if_inner_dirty(opts.project_path)

    def _start_services(self) -> None:
        ctx = self.ctx
        opts = self.options

        if not opts.dry_run and not opts.no_dashboard:
            dashboard = DashboardProcess(opts.project_path, open_browser=opts.open_browser)
            url = dashboard.start()
            if url:
                ctx.dashboard_url = url

        if not opts.dry_run and opts.blueprint_server_flag:
            blueprint = BlueprintServerProcess(opts.project_path)
            ctx.blueprint_server = blueprint
            ctx.blueprint_url = blueprint.start()

    # ── per-iteration ──────────────────────────────────────────────────

    def _run_iteration(self, i: int) -> bool:
        """Run one iteration. Returns False if the outer loop should break."""
        ctx = self.ctx
        ctx.iter_index = i
        ctx.current_stage = read_stage(ctx.progress_file, ctx.force_stage())

        if is_complete(ctx.progress_file, ctx.force_stage()):
            log.success("PROGRESS.md says COMPLETE. Exiting loop.")
            return False

        # ``--from <phase>`` only affects the first iteration; subsequent
        # iterations always run the full plan→prover→review.
        ctx.skip_now = self.options.skip_first_iter if i == 0 else set()
        if ctx.skip_now:
            log.info(
                f"--from set: skipping {', '.join(sorted(ctx.skip_now))} on this iteration."
            )

        log.iteration(
            i + 1, self.options.max_iterations, ctx.current_stage, ctx.project_name,
        )
        if ctx.dashboard_url:
            log.step(f"Live view: {ctx.dashboard_url}")
        if ctx.blueprint_url:
            log.step(f"Blueprint: {ctx.blueprint_url}")

        iter_start = time.monotonic()
        self._setup_iteration_dir(i)

        # Phase 0a — Pre-compact (plan side)
        # Shrinks STRATEGY.md / task_pending.md / task_done.md before
        # the plan agent reads them. Per-target threshold gating means
        # this is silent on small/early-iteration projects.
        PreCompactPlanPhase(ctx).run()

        # Phase 1 — Plan
        if PlanPhase(ctx).run().completed:
            return False

        # Phase 1b — Validate the plan's PROGRESS.md output.
        # Deterministic regex rewrites first (catch heading drift like
        # ``## Strategy`` → ``## Current Objectives``); if still no
        # parseable objectives, append a corrective hint to
        # USER_HINTS.md and skip prover/review for this iteration. This
        # short-circuits the silent-loop failure where the orchestrator
        # parser rejects PROGRESS.md, prover dispatches nothing, and
        # review misdiagnoses the empty round as a mathematical dead
        # end — burning iterations until the user notices.
        if not plan_validate.validate_plan_output(ctx):
            log.warn(
                f"Iteration {i + 1}: skipping prover/review — plan-validate "
                f"failed. Next plan agent will see USER_HINTS.md."
            )
            iter_secs = int(time.monotonic() - iter_start)
            if not ctx.dry_run and ctx.iter_meta is not None:
                write_meta(
                    ctx.iter_meta,
                    completedAt=utcnow_iso(),
                    wallTimeSecs=iter_secs,
                    status="plan_validate_failed",
                )
            return True

        # Phase 2 — Prover
        ProverPhase(ctx).run()

        # Phase 2b — Sync \leanok markers deterministically.
        # Replaces the review agent's mechanical marker placement;
        # review still owns \mathlibok and prose.
        SyncLeanokPhase(ctx).run()

        # Phase 3a — Pre-compact (review side)
        PreCompactReviewPhase(ctx).run()

        # Phase 3 — Review
        ReviewPhase(ctx).run()

        # Post-iteration: sorry count + finalize
        self._post_phases_sorry_count()
        FinalizePhase(ctx).run()

        # Try to start the deferred blueprint server now that finalize
        # may have produced blueprint/web/.
        if (self.options.blueprint_server_flag
                and ctx.blueprint_url is None
                and ctx.blueprint_server is not None):
            ctx.blueprint_url = ctx.blueprint_server.try_start_deferred()

        iter_secs = int(time.monotonic() - iter_start)
        log.info(f"Iteration {i + 1} complete ({iter_secs}s)")
        if not ctx.dry_run:
            write_meta(ctx.iter_meta, completedAt=utcnow_iso(), wallTimeSecs=iter_secs)
            data = cost_summary(ctx.iter_dir)
            if data:
                log.cost_table(
                    f"Iteration {i + 1}",
                    data.totals_dict(),
                    data.model_rows() or None,
                )
        return True

    def _setup_iteration_dir(self, i: int) -> None:
        ctx = self.ctx
        opts = self.options
        ctx.iter_dir = None
        ctx.iter_meta = None
        ctx.iter_num = 0
        if opts.dry_run:
            return

        # When --from <phase> or --resume is used on the FIRST iteration
        # the user wants to RESUME the previous run, not start fresh.
        # Reuse the most recent iter-NNN dir so logs / snapshots / phase-
        # stamped meta entries land alongside whatever the earlier run
        # produced. Subsequent iterations always create new dirs as
        # usual. ``skip_now`` is empty for ``--from plan`` (nothing to
        # skip), so the trigger here is ``opts.from_phase`` / opts.resume
        # — both indicate user intent to retry-in-place.
        reuse_last = (
            i == 0
            and (
                bool(ctx.skip_now)
                or opts.from_phase is not None
                or opts.resume
            )
            and ctx.log_dir.exists()
        )
        last_iter = None
        if reuse_last:
            existing = sorted(
                int(d.name[5:]) for d in ctx.log_dir.iterdir()
                if d.is_dir() and d.name.startswith('iter-')
                and d.name[5:].isdigit()
            )
            if existing:
                last_iter = existing[-1]

        if last_iter is not None:
            ctx.iter_num = last_iter
            ctx.iter_dir = ctx.log_dir / f"iter-{ctx.iter_num:03d}"
            trigger = "--resume" if opts.resume else "--from"
            log.info(
                f"{trigger}: reusing iter-{ctx.iter_num:03d} "
                f"(existing log dir, no new iteration created)"
            )
        else:
            ctx.iter_num = next_iter_num(ctx.log_dir)
            ctx.iter_dir = ctx.log_dir / f"iter-{ctx.iter_num:03d}"

        ctx.iter_meta = ctx.iter_dir / "meta.json"
        ctx.iter_dir.mkdir(parents=True, exist_ok=True)
        if opts.parallel:
            (ctx.iter_dir / "provers").mkdir(exist_ok=True)

        # Resolve --resume's target phase from the prior meta.json
        # BEFORE the plan.status="running" write below clobbers prior
        # status fields. Adjusts ctx.skip_now so phases before the
        # resumed one get marked "skipped" as usual.
        self._resolve_resume_target(i)

        # Don't clobber the existing meta.json on a resume — only write
        # the bootstrap fields if the file didn't already exist (or is
        # empty).
        if not ctx.iter_meta.exists() or ctx.iter_meta.stat().st_size == 0:
            write_meta(
                ctx.iter_meta,
                iteration=ctx.iter_num,
                stage=ctx.current_stage,
                mode="parallel" if opts.parallel else "serial",
                startedAt=utcnow_iso(),
            )
        write_meta(ctx.iter_meta, **{
            "plan.status": "running" if "plan" not in ctx.skip_now else "skipped",
        })
        log.step(f"Log dir: {ctx.iter_dir}")

    def _resolve_resume_target(self, i: int) -> None:
        """Pick the phase whose session should be resumed and adjust
        ``ctx.skip_now`` so earlier phases are marked skipped.

        Runs on iter 0 only. Three cases:
          * ``--from <phase> --resume``: target is ``opts.from_phase``.
          * ``--resume`` alone: target is detected from prior meta.json
            by :func:`detect_last_interrupted_phase`; falls back to
            ``"plan"`` when every phase is already ``"done"`` (the
            iteration finished cleanly and the user re-issued --resume).
          * ``--from <phase>`` without ``--resume``: no resume; this is
            the legacy fresh-restart-at-phase behavior, unchanged.
        """
        ctx = self.ctx
        opts = self.options
        if i != 0 or not opts.resume:
            return

        if opts.from_phase is not None:
            target = opts.from_phase
        else:
            from .resume import detect_last_interrupted_phase
            target = detect_last_interrupted_phase(ctx.iter_meta)
            if target is None:
                log.info(
                    "--resume: prior iter has every phase marked 'done'; "
                    "nothing to continue, running plan fresh."
                )
                target = "plan"
            else:
                log.info(f"--resume: auto-detected interrupted phase = {target}")

        ctx.resolved_resume_phase = target
        ctx.skip_now = parse_from_phase(target)

    def _post_phases_sorry_count(self) -> None:
        ctx = self.ctx
        ctx.sorry_after = None
        if ctx.dry_run:
            return
        sorry_after = count_sorries(ctx.project_path)
        ctx.sorry_after = sorry_after
        if sorry_after is None:
            return
        write_meta(ctx.iter_meta, sorry_count=sorry_after)
        if ctx.prev_sorry is not None:
            delta = ctx.prev_sorry - sorry_after
            if delta > 0:
                log.success(
                    f"Sorry count: {ctx.prev_sorry} -> {sorry_after} "
                    f"({delta} resolved this iteration)"
                )
            elif delta == 0:
                log.warn(f"Sorry count unchanged: {sorry_after}")
            else:
                log.info(
                    f"Sorry count: {ctx.prev_sorry} -> {sorry_after} "
                    f"({-delta} new — likely from refactoring)"
                )
        else:
            log.info(f"Sorry count: {sorry_after}")
        ctx.prev_sorry = sorry_after

    # ── summary ────────────────────────────────────────────────────────

    def _summarize(self) -> None:
        ctx = self.ctx
        opts = self.options
        loop_secs = int(time.monotonic() - self.loop_start)

        if not is_complete(ctx.progress_file, ctx.force_stage()):
            log.warn(f"Reached max iterations ({opts.max_iterations}). Stopping.")

        if not ctx.dry_run:
            final_sorry = count_sorries(ctx.project_path)
            if final_sorry is not None and ctx.initial_sorry is not None:
                resolved_count = ctx.initial_sorry - final_sorry
                log.info(
                    f"Sorries: {ctx.initial_sorry} -> {final_sorry} "
                    f"({resolved_count} resolved)"
                )
            elif final_sorry is not None:
                log.info(f"Final sorry count: {final_sorry}")

        log.info(f"Total wall time: {loop_secs}s")
        data = cost_summary(ctx.log_dir)
        if data:
            log.cost_table(
                "Loop totals (Note: This is indicative, it doesn't take into "
                "account pro subscriptions for instance)",
                data.totals_dict(),
                data.model_rows() or None,
            )

        if ctx.dashboard_url:
            log.panel(
                f"Loop finished. The dashboard is still running at "
                f"[bold cyan]{ctx.dashboard_url}[/bold cyan].\n"
                + (
                    f"Blueprint preview: [bold cyan]{ctx.blueprint_url}[/bold cyan]\n"
                    if ctx.blueprint_url else ""
                )
                + "Inspect results, then stop it with Ctrl-C or by closing this terminal.",
                title="Done",
                style="green",
            )


def parse_from_phase(from_phase: str | None) -> set[str]:
    """Resolve `--from <phase>` to the set of phases to skip on iter 1."""
    skip: set[str] = set()
    if not from_phase:
        return skip
    from_norm = from_phase.strip().lower()
    if from_norm not in _PHASES:
        log.error(
            f"--from must be one of {', '.join(_PHASES)} "
            f"(got '{from_phase}')"
        )
        raise typer.Exit(2)
    for ph in _PHASES:
        if ph == from_norm:
            break
        skip.add(ph)
    return skip
