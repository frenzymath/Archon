"""Finalize phase: per-iteration git commit / lake build / leanblueprint web."""

from __future__ import annotations

from archon import log
from archon.commands.tooling.iteration import (
    IterationFinalizationReport,
    IterationFinalizer,
)
from archon.state import write_meta

from .base import Phase, PhaseResult


class FinalizePhase(Phase):
    name = "Finalize (git / lake / blueprint)"
    number = 5
    skip_token = ""  # not affected by --from

    def run(self) -> PhaseResult:
        ctx = self.ctx
        if ctx.dry_run or not ctx.options.has_finalize:
            return PhaseResult(skipped=True)

        log.phase(self.number, self.name)
        report = self._invoke_finalizer()
        write_meta(ctx.iter_meta, **report.to_meta_dict())
        for w in report.warnings:
            log.warn(w)
        return PhaseResult()

    def _invoke_finalizer(self) -> IterationFinalizationReport:
        ctx = self.ctx
        finalizer = IterationFinalizer(
            ctx.project_path,
            do_git=ctx.options.do_git,
            do_lake_build=ctx.options.do_lake,
            do_blueprint_web=ctx.options.do_bp_web,
        )
        return finalizer.run(
            iter_num=ctx.iter_num,
            stage=ctx.current_stage,
            sorry_count=ctx.sorry_after,
        )
