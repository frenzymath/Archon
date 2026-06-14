"""DagBuildPhase — deterministically rebuild the leandag DAG artifacts.

After elaboration and the blueprint doctor have run, regenerate the
machine-readable graph (``.leandag/dag.json``) and leandag's interactive
``.leandag/graph.html`` from the current ``.lean`` files and blueprint
chapters. Running this in the loop (rather than only on demand from the
dag agent) means the dashboard's DAG page and the standalone graph always
reflect the latest iteration without anyone having to run ``leandag build``
by hand.
"""

from __future__ import annotations

import time

from archon import log

from ..leandag_gaps import build_artifacts
from .base import DagPhase, DagPhaseResult


class DagBuildPhase(DagPhase):
    name = "DAG build + html"
    number = 3

    def run(self) -> DagPhaseResult:
        ctx = self.ctx
        if ctx.dry_run:
            log.phase(self.number, f"{self.name} — skipped (--dry-run)")
            return DagPhaseResult(skipped=True)

        start = time.monotonic()
        ok, error = build_artifacts(ctx.project_path)
        secs = int(time.monotonic() - start)

        if ok:
            log.success(
                f"leandag build: refreshed .leandag/dag.json and graph.html ({secs}s)"
            )
        else:
            # Non-fatal: a missing/unparseable blueprint early in the run
            # shouldn't abort the loop — the next iteration will retry.
            log.warn(f"leandag build skipped: {error}")
        return DagPhaseResult()
