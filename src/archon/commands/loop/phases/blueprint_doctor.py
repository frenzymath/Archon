"""BlueprintDoctorPhase — structural lints for the project blueprint.

Runs after the prover phase (alongside ``sync_leanok``), before the
review agent gets dispatched. Detects classes of bug that have
repeatedly slipped past the (mandatory) blueprint-reviewer subagent:

1. ``.tex`` files under ``blueprint/src/chapters/`` that are not
   ``\\input``'d by ``content.tex`` — orphan chapters.
2. ``\\ref{...}`` / ``\\uses{...}`` / ``\\cref{...}`` (etc.) targets
   that no ``\\label{...}`` defines — broken cross-references.
3. Empty-argument annotations (``\\uses{}``, ``\\label{}``, ...) and
   empty list items (``\\uses{a,,b}``) — malformed annotations that
   crash plastex with ``Label '' could not be resolved`` followed by
   a depgraph ``RecursionError``.

The doctor is *informational*. It writes a Markdown report + a JSON
sidecar to ``.archon/logs/iter-NNN/blueprint-doctor.{md,json}`` and
logs a one-line summary; nothing gets mutated.
"""

from __future__ import annotations

import time

from archon import log

from ..blueprint_doctor import run_blueprint_doctor, write_reports
from .base import Phase, PhaseResult


class BlueprintDoctorPhase(Phase):
    """Deterministic structural lints for the blueprint between prover and review."""

    name = "Blueprint doctor"
    skip_token = "blueprint-doctor"

    def run(self) -> PhaseResult:
        ctx = self.ctx
        if self.skip_token in ctx.skip_now:
            log.phase(0, f"{self.name} — skipped (--from)")
            return PhaseResult(skipped=True)
        if ctx.dry_run:
            log.phase(0, f"{self.name} — skipped (--dry-run)")
            return PhaseResult(skipped=True)

        start = time.monotonic()
        try:
            report = run_blueprint_doctor(ctx.project_path)
        except Exception as e:  # defensive: doctor must never break the loop
            log.warn(f"blueprint-doctor crashed: {e}")
            return PhaseResult()

        if report is None:
            # No blueprint to lint — silent skip.
            return PhaseResult()

        secs = int(time.monotonic() - start)

        if ctx.iter_dir is None:
            # We're outside an iter sidecar (shouldn't happen for the
            # loop path); skip persistence but still log a summary.
            if report.has_findings:
                log.warn(
                    f"blueprint-doctor: {len(report.orphan_chapters)} orphan "
                    f"chapter(s), {len(report.broken_refs)} broken "
                    f"reference(s), {len(report.malformed_refs)} malformed "
                    f"annotation(s) ({secs}s) — no iter sidecar to persist into"
                )
            else:
                log.success(f"blueprint-doctor: clean ({secs}s)")
            return PhaseResult()

        json_path, md_path = write_reports(
            report, ctx.iter_dir, ctx.project_path,
        )

        if not report.has_findings:
            log.success(f"blueprint-doctor: clean ({secs}s)")
        else:
            log.warn(
                f"blueprint-doctor: {len(report.orphan_chapters)} orphan "
                f"chapter(s), {len(report.broken_refs)} broken reference(s), "
                f"{len(report.malformed_refs)} malformed annotation(s), "
                f"{len(report.axiom_decls)} axiom decl(s) "
                f"({secs}s) — see {md_path}"
            )
        return PhaseResult()
