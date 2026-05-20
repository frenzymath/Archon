"""AxiomSweepPhase — deterministic ``sorryAx``-laundering detector.

Runs after ``sync_leanok`` / ``blueprint-doctor`` and before review,
ONLY when ``loop.axiom_sweep`` is true (off by default — it recompiles
every Lean file, so it is much slower than the other deterministic
checks). Writes ``axiom-sweep.{md,json}`` to the iter log dir and logs a
one-line summary. Never blocks the loop and never mutates project state
beyond the underlying script's own backup/restore.
"""

from __future__ import annotations

from archon import log
from archon.commands.tooling.project_config import load_project_config

from ..axiom_sweep import run_axiom_sweep, write_reports
from .base import Phase, PhaseResult


class AxiomSweepPhase(Phase):
    """Optional ``#print axioms`` sweep for sorryAx laundering."""

    name = "Axiom sweep"
    skip_token = "axiom-sweep"

    def run(self) -> PhaseResult:
        ctx = self.ctx
        if self.skip_token in ctx.skip_now:
            log.phase(0, f"{self.name} — skipped (--from)")
            return PhaseResult(skipped=True)
        if ctx.dry_run:
            log.phase(0, f"{self.name} — skipped (--dry-run)")
            return PhaseResult(skipped=True)

        cfg = load_project_config(ctx.project_path)
        if not bool(cfg.loop_section().get("axiom_sweep")):
            # Off by default — silent skip.
            return PhaseResult()

        log.phase(0, self.name)
        try:
            report = run_axiom_sweep(ctx.project_path)
        except Exception as e:  # defensive: the sweep must never break the loop
            log.warn(f"axiom sweep crashed: {e}")
            return PhaseResult()

        if report is None:
            # No Lean project / script missing — silent skip.
            return PhaseResult()

        if ctx.iter_dir is not None:
            _, md_path = write_reports(report, ctx.iter_dir, ctx.project_path)
        else:
            md_path = None

        if not report.ran:
            log.warn(f"axiom sweep did not complete: {report.error}")
        elif report.has_launderings:
            n = len(report.sorry_launderings)
            where = f" — see {md_path}" if md_path else ""
            log.warn(
                f"axiom sweep: {n} sorryAx-laundering decl(s) that emit NO "
                f"sorry warning ({report.duration_s}s){where}"
            )
        else:
            log.success(
                f"axiom sweep: no sorryAx laundering "
                f"({report.files_checked} files, {report.duration_s}s)"
            )
        return PhaseResult()
