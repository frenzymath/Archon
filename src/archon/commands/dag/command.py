"""DagCommand: orchestrates the DAG blueprint elaboration loop.

The Typer-decorated `dag` function lives in :mod:`.entry`.
`DagCommand.run()` is the behavior: bootstrap context, iterate
elaboration + doctor phases, stop when COMPLETE or max iterations hit.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from archon import log
from archon.agent import QuotaExhaustedError
from archon.dispatch import (
    MAX_PARALLEL_ENV_VAR,
    SLOTS_ENV_VAR,
    SlotPool,
)
from archon.commands.tooling.version import warn_if_mismatch
from archon.state import (
    cost_summary,
    init_iter_sidecar_dir,
    next_iter_num,
    utcnow_iso,
    write_meta,
)
from archon.commands.loop.preflight import (
    check_informal_agent_keys,
    preflight,
)
from archon.commands.loop.services import BlueprintServerProcess, DashboardProcess

from .context import DagContext, DagOptions
from .phases import DagBuildPhase, DagDoctorPhase, DagElaborationPhase
from .status import dag_is_complete, set_dag_in_progress


# Subagents the blueprint-elaboration phase always has available,
# regardless of the project's ``subagents.enabled`` config. The dag
# agent's job is to dispatch these; gating them behind config made a
# fresh `archon dag` run dispatch nothing. Forced on via the
# ARCHON_FORCE_SUBAGENTS env var (see project_config.apply_forced_subagents).
DAG_SUBAGENTS = (
    "blueprint-writer",
    "blueprint-reviewer",
    "reference-retriever",
    "strategy-critic",
    "dag-walker",
)


def _ensure_dag_prompt(state_dir: Path) -> None:
    """Install dag.md into .archon/prompts/ if it isn't already there."""
    dst = state_dir / "prompts" / "dag.md"
    if dst.exists():
        return
    try:
        from archon.commands.init.utils import data_path
        src = data_path("prompts/dag.md")
        if src.is_file():
            import shutil
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    except Exception:
        pass  # graceful — agent will warn if the file is missing


class DagCommand:
    """Orchestrates one full `archon dag` invocation."""

    def __init__(self, options: DagOptions) -> None:
        self.options = options
        self.ctx: DagContext | None = None
        self.start: float = 0.0

    def run(self) -> None:
        self._bootstrap_context()
        # Force-enable the blueprint subagents for this whole run. Set in
        # os.environ (not just the agent's env_overrides) so it reaches
        # BOTH the in-process prompt builder — which renders the subagent
        # catalog — and the child `claude` / `archon-subagent.py`
        # dispatches that inherit this process's environment.
        from archon.commands.tooling.project_config import FORCE_SUBAGENTS_ENV
        os.environ[FORCE_SUBAGENTS_ENV] = ",".join(DAG_SUBAGENTS)
        self._announce()
        self._start_services()

        ctx = self.ctx
        if not ctx.dry_run:
            check_informal_agent_keys()

        # The user explicitly launched `archon dag`, so we do NOT short-circuit
        # on a prior COMPLETE marker — we demote it to IN_PROGRESS and run at
        # least one more elaboration pass. If the blueprint is in fact still
        # done, the agent re-asserts COMPLETE at the end of that pass and the
        # loop stops after one iteration.
        if dag_is_complete(ctx.state_dir):
            log.info(
                f"DAG_STATUS.md was COMPLETE for '{ctx.project_name}', but you "
                f"launched `archon dag` — resetting to IN_PROGRESS and running "
                f"another pass. The agent re-asserts COMPLETE at the end if the "
                f"blueprint is still done."
            )
            if not ctx.dry_run:
                set_dag_in_progress(ctx.state_dir)

        self.start = time.monotonic()

        for i in range(self.options.max_iterations):
            try:
                if not self._run_iteration(i):
                    break
            except QuotaExhaustedError as exc:
                log.error(
                    f"API quota exhausted: {exc}\n"
                    f"Stopping. Resume with `archon dag` after the limit resets."
                )
                break

        self._summarize()

    # ── bootstrap ──────────────────────────────────────────────────────

    def _bootstrap_context(self) -> None:
        opts = self.options
        resolved = opts.project_path
        state_dir = resolved / ".archon"
        log_dir = state_dir / "logs"

        preflight(resolved, state_dir, opts.dry_run, backend=opts.backend)

        if not opts.dry_run:
            log_dir.mkdir(parents=True, exist_ok=True)
            _ensure_dag_prompt(state_dir)

        self.ctx = DagContext(
            options=opts,
            project_path=resolved,
            project_name=resolved.name,
            state_dir=state_dir,
            log_dir=log_dir,
        )

    def _announce(self) -> None:
        ctx = self.ctx
        opts = self.options

        config_panel = {
            "Project": str(opts.project_path),
            "Max iterations": str(opts.max_iterations),
            "Model": opts.model,
            "Lean-aware": "yes" if opts.lean_aware else "no",
            "Dashboard": "disabled" if opts.no_dashboard else "enabled",
            "Blueprint server": "enabled" if opts.blueprint_server_flag else "disabled",
            "Logs": str(ctx.log_dir),
        }
        if opts.dry_run:
            config_panel["Mode"] = "[yellow]DRY RUN[/yellow]"

        log.header("Archon DAG")
        log.key_value(config_panel)
        warn_if_mismatch(opts.project_path)

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

        log.iteration(i + 1, self.options.max_iterations, "dag", ctx.project_name)
        if ctx.dashboard_url:
            log.step(f"Live view: {ctx.dashboard_url}")
        if ctx.blueprint_url:
            log.step(f"Blueprint: {ctx.blueprint_url}")

        iter_start = time.monotonic()
        self._setup_iteration_dir(i)

        # Phase 1 — Elaboration
        result = DagElaborationPhase(ctx).run()

        # Phase 2 — Blueprint doctor
        DagDoctorPhase(ctx).run()

        # Phase 3 — Rebuild leandag artifacts (.leandag/dag.json + graph.html)
        DagBuildPhase(ctx).run()

        # Try to start the deferred blueprint server now that elaboration
        # may have produced blueprint/web/.
        if (
            self.options.blueprint_server_flag
            and ctx.blueprint_url is None
            and ctx.blueprint_server is not None
        ):
            ctx.blueprint_url = ctx.blueprint_server.try_start_deferred()

        iter_secs = int(time.monotonic() - iter_start)
        log.info(f"DAG iteration {i + 1} complete ({iter_secs}s)")
        if not ctx.dry_run:
            write_meta(
                ctx.iter_meta,
                completedAt=utcnow_iso(),
                wallTimeSecs=iter_secs,
            )
            data = cost_summary(ctx.iter_dir)
            if data:
                log.cost_table(
                    f"Iteration {i + 1}",
                    data.totals_dict(),
                    data.model_rows() or None,
                )
                log.operations_table(data.operations)

        if result.complete:
            log.success(
                "DAG_STATUS.md says COMPLETE. "
                "Blueprint is ready for `archon loop`."
            )
            return False

        return True

    def _setup_iteration_dir(self, i: int) -> None:
        ctx = self.ctx
        opts = self.options
        ctx.iter_dir = None
        ctx.iter_meta = None
        ctx.iter_num = 0
        if opts.dry_run:
            return

        ctx.iter_num = next_iter_num(ctx.log_dir)
        ctx.iter_dir = ctx.log_dir / f"iter-{ctx.iter_num:03d}"
        ctx.iter_meta = ctx.iter_dir / "meta.json"
        ctx.iter_dir.mkdir(parents=True, exist_ok=True)

        slots_dir = ctx.iter_dir / ".slots"
        SlotPool.init(slots_dir, opts.max_parallel)
        os.environ[SLOTS_ENV_VAR] = str(slots_dir)
        os.environ[MAX_PARALLEL_ENV_VAR] = str(opts.max_parallel)

        init_iter_sidecar_dir(ctx.state_dir, ctx.iter_num)

        write_meta(
            ctx.iter_meta,
            iteration=ctx.iter_num,
            stage="dag",
            mode="dag",
            startedAt=utcnow_iso(),
            **{"dag.status": "running"},
        )
        log.step(f"Log dir: {ctx.iter_dir}")

    # ── summary ────────────────────────────────────────────────────────

    def _summarize(self) -> None:
        ctx = self.ctx
        opts = self.options
        wall_secs = int(time.monotonic() - self.start)

        if not dag_is_complete(ctx.state_dir):
            log.warn(
                f"Reached max iterations ({opts.max_iterations}). "
                f"Blueprint may be incomplete — re-run `archon dag` to continue."
            )
        else:
            log.success(
                f"DAG elaboration complete in {wall_secs}s. "
                f"Run `archon loop` to start formal proof work."
            )

        data = cost_summary(ctx.log_dir)
        if data:
            log.cost_table(
                "DAG totals",
                data.totals_dict(),
                data.model_rows() or None,
            )
            log.operations_table(data.operations)

        if ctx.dashboard_url:
            log.panel(
                f"DAG finished. The dashboard is still running at "
                f"[bold cyan]{ctx.dashboard_url}[/bold cyan].\n"
                + (
                    f"Blueprint preview: [bold cyan]{ctx.blueprint_url}[/bold cyan]\n"
                    if ctx.blueprint_url else ""
                )
                + "Inspect results, then stop it with Ctrl-C or by closing this terminal.",
                title="Done",
                style="green",
            )
