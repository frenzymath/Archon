"""DagDoctorPhase — structural lints on the blueprint after elaboration."""

from __future__ import annotations

import time

from archon import log
from archon.commands.loop.blueprint_doctor import run_blueprint_doctor, write_reports

from .base import DagPhase, DagPhaseResult


class DagDoctorPhase(DagPhase):
    name = "Blueprint doctor"
    number = 2

    def run(self) -> DagPhaseResult:
        ctx = self.ctx
        if ctx.dry_run:
            log.phase(self.number, f"{self.name} — skipped (--dry-run)")
            return DagPhaseResult(skipped=True)

        start = time.monotonic()
        try:
            report = run_blueprint_doctor(ctx.project_path)
        except Exception as e:
            log.warn(f"blueprint-doctor crashed: {e}")
            return DagPhaseResult()

        if report is None:
            return DagPhaseResult()

        secs = int(time.monotonic() - start)

        if ctx.iter_dir is not None:
            write_reports(report, ctx.iter_dir, ctx.project_path)

        if not report.has_findings:
            log.success(f"blueprint-doctor: clean ({secs}s)")
        else:
            log.warn(
                f"blueprint-doctor: {len(report.orphan_chapters)} orphan "
                f"chapter(s), {len(report.broken_refs)} broken reference(s), "
                f"{len(report.malformed_refs)} malformed annotation(s) "
                f"({secs}s)"
            )
        return DagPhaseResult()
