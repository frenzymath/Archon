"""Prover phase: dispatch to serial / parallel / multilane based on options."""

from __future__ import annotations

import time

from archon import log
from archon.commands.tooling.iteration import commit_phase
from archon.multilane.config import multilane_config_from_simple
from archon.state import write_meta

from ..lane_round import LaneRoundExecutor, LaneRoundPreviewRunner
from ..prover import ParallelProverRunner, SerialProverRunner
from ..sorry_count import count_sorries
from .base import Phase, PhaseResult


class ProverPhase(Phase):
    name = "Prover agent(s)"
    number = 2
    skip_token = "prover"

    def run(self) -> PhaseResult:
        ctx = self.ctx
        if self.skip_token in ctx.skip_now:
            log.phase(self.number, f"{self.name} — skipped (--from)")
            from archon.state import read_stage
            ctx.current_stage = read_stage(ctx.progress_file, ctx.force_stage())
            return PhaseResult(skipped=True)

        mode = "parallel" if ctx.options.parallel else "serial"
        log.phase(self.number, f"{self.name} — {mode}")

        prover_start = time.monotonic()
        if not ctx.dry_run:
            write_meta(ctx.iter_meta, **{"prover.status": "running"})

        self._dispatch()

        prover_secs = int(time.monotonic() - prover_start)
        log.info(f"Prover phase finished ({prover_secs}s)")
        if ctx.dashboard_url:
            log.step(f"Inspect diffs: {ctx.dashboard_url}/diffs")
        if not ctx.dry_run:
            write_meta(ctx.iter_meta, **{
                "prover.status": "done",
                "prover.durationSecs": prover_secs,
            })
            mid_sorry = count_sorries(ctx.project_path)
            commit_phase(
                ctx.project_path, iter_num=ctx.iter_num, phase="prover",
                summary=(
                    f"all-provers sorry={mid_sorry} ({prover_secs}s)"
                    if mid_sorry is not None
                    else f"all-provers ({prover_secs}s)"
                ),
            )
        return PhaseResult()

    def _dispatch(self) -> None:
        ctx = self.ctx
        if ctx.options.multilane_preview:
            self._run_multilane_preview()
        elif ctx.options.multilane_execute:
            # Multilane has its own lane-round executor with bespoke
            # state; piping --resume through it is a larger change and
            # not in scope here. Surface the gap so the user isn't
            # silently surprised by a fresh run.
            if self._resume_enabled():
                log.warn(
                    "--resume is not yet supported in multilane mode; "
                    "running prover fresh."
                )
            self._run_multilane_execute()
        elif ctx.options.parallel:
            self._run_parallel()
        else:
            self._run_serial()

    def _run_multilane_preview(self) -> None:
        ctx = self.ctx
        runner = LaneRoundPreviewRunner(
            project_path=ctx.project_path,
            state_dir=ctx.state_dir,
            progress_file=ctx.progress_file,
            stage=ctx.current_stage,
            iteration=ctx.iter_num,
        )
        preview_info = runner.run()
        if not ctx.dry_run and preview_info is not None:
            write_meta(ctx.iter_meta, **{
                "prover.mode": "multilane-preview",
                "prover.multilanePreview": True,
                "prover.multilaneReport": preview_info["report_path"],
                "prover.multilaneAssignments":
                    preview_info["summary"].get("assignment_count", 0),
                "prover.multilaneAssignmentsJsonl": preview_info["assignments_path"],
                "prover.multilanePreparedJsonl": preview_info["prepared_path"],
                "prover.multilaneResultsJsonl": preview_info["results_path"],
            })

    def _run_multilane_execute(self) -> None:
        ctx = self.ctx
        # Build lane config off the project-level config.json multilane
        # section. This is the new path; the older
        # .archon/multilane/config.* file is no longer required.
        lane_config = multilane_config_from_simple(ctx.options.multilane_cfg)
        executor = LaneRoundExecutor(
            project_name=ctx.project_name,
            project_path=ctx.project_path,
            state_dir=ctx.state_dir,
            progress_file=ctx.progress_file,
            stage=ctx.current_stage,
            iteration=ctx.iter_num,
            verbose_logs=ctx.verbose_logs,
            model=ctx.model,
            max_parallel=ctx.options.max_parallel,
            config=lane_config,
        )
        execution_info = executor.run()
        if not ctx.dry_run and execution_info is not None:
            write_meta(ctx.iter_meta, **{
                "prover.mode": "multilane-execute",
                "prover.multilaneExecute": True,
                "prover.multilaneReport": execution_info["report_path"],
                "prover.multilaneAssignments":
                    execution_info["summary"].get("assignment_count", 0),
                "prover.multilaneAssignmentsJsonl": execution_info["assignments_path"],
                "prover.multilanePreparedJsonl": execution_info["prepared_path"],
                "prover.multilaneResultsJsonl": execution_info["results_path"],
                "prover.promotedFiles": execution_info.get("promoted_files", []),
                "prover.promotionCommit": execution_info.get("promotion_commit"),
            })

    def _resume_enabled(self) -> bool:
        return self.ctx.resume_phase == self.skip_token

    def _run_parallel(self) -> None:
        ctx = self.ctx
        runner = ParallelProverRunner(
            project_name=ctx.project_name,
            project_path=ctx.project_path,
            state_dir=ctx.state_dir,
            stage=ctx.current_stage,
            iter_dir=ctx.iter_dir,
            iter_meta=ctx.iter_meta,
            iter_num=ctx.iter_num,
            max_parallel=ctx.options.max_parallel,
            max_objectives=ctx.options.max_objectives,
            block_on_blocked_deps=ctx.options.block_on_blocked_deps,
            verbose_logs=ctx.verbose_logs,
            model=ctx.model,
            dashboard_url=ctx.dashboard_url,
            blueprint_url=ctx.blueprint_url,
            debug_feedback=ctx.options.debug_feedback,
            resume_enabled=self._resume_enabled(),
            harness=ctx.harness_for("prover"),
        )
        runner.run(dry_run=ctx.dry_run)

    def _run_serial(self) -> None:
        ctx = self.ctx
        runner = SerialProverRunner(
            project_name=ctx.project_name,
            project_path=ctx.project_path,
            state_dir=ctx.state_dir,
            stage=ctx.current_stage,
            iter_dir=ctx.iter_dir,
            iter_num=ctx.iter_num,
            verbose_logs=ctx.verbose_logs,
            model=ctx.model,
            debug_feedback=ctx.options.debug_feedback,
            iter_meta=ctx.iter_meta,
            resume_enabled=self._resume_enabled(),
            harness=ctx.harness_for("prover"),
        )
        runner.run(dry_run=ctx.dry_run, progress_file=ctx.progress_file)
