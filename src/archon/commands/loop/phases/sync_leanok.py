"""SyncLeanokPhase — deterministic blueprint marker sync.

Runs after the prover phase and before the review-side compactor.
Walks every ``blueprint/src/chapters/*.tex`` and updates each
declaration block's ``\\leanok`` markers based on the actual sorry
count + compilation status of the corresponding Lean source.

This replaces the review agent's mechanical marker placement (a
read-write-heavy job that the review prompt previously enumerated in
prose). The review agent retains responsibility for ``\\mathlibok``
and ``% NOTE: ...`` annotations — those still require semantic
judgement.

A single inner-git commit ``archon[NNN/marker-sync]`` captures the
diff so the user can audit before proceeding to review. When the
script finds nothing to change, no commit is made.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from archon import log
from archon.commands.tooling.iteration import commit_phase

from .base import Phase, PhaseResult


def _script_path() -> Path:
    """Locate the bundled ``sync_leanok.py`` regardless of install layout.

    Mirrors the lookup used by ``count_sorries`` — the script lives
    next to ``sorry_analyzer.py`` under our package's data tree.
    """
    from archon.commands.init.utils import data_path
    return data_path("skills/lean4/lib/scripts/sync_leanok.py")


class SyncLeanokPhase(Phase):
    """Deterministic ``\\leanok`` marker sync between prover and review."""
    name = "Sync \\leanok markers"
    skip_token = "marker-sync"

    def run(self) -> PhaseResult:
        ctx = self.ctx
        if self.skip_token in ctx.skip_now:
            log.phase(0, f"{self.name} — skipped (--from)")
            return PhaseResult(skipped=True)
        if ctx.dry_run:
            log.phase(0, f"{self.name} — skipped (--dry-run)")
            return PhaseResult(skipped=True)

        # No blueprint → nothing to do (silent skip).
        if not (ctx.project_path / "blueprint" / "src" / "chapters").is_dir():
            return PhaseResult()

        script = _script_path()
        if not script.exists():
            log.warn(f"sync_leanok script not found at {script}")
            return PhaseResult()

        log.phase(0, self.name)

        # Run the script once in JSON mode so we can summarize and
        # decide whether a commit is warranted without re-parsing the
        # human-readable output.
        start = time.monotonic()
        try:
            r = subprocess.run(
                [sys.executable, str(script), str(ctx.project_path),
                 "--format=json"],
                capture_output=True, text=True, timeout=600,
            )
        except (OSError, subprocess.SubprocessError) as e:
            log.warn(f"sync_leanok failed to run: {e}")
            return PhaseResult()
        secs = int(time.monotonic() - start)

        if r.returncode != 0:
            log.warn(
                f"sync_leanok exited {r.returncode}; "
                f"stderr (truncated): {(r.stderr or '').strip()[:200]}"
            )
            return PhaseResult()

        try:
            changes = json.loads(r.stdout or "[]")
        except json.JSONDecodeError:
            changes = []

        if not isinstance(changes, list) or not changes:
            log.success(f"sync_leanok: no marker changes ({secs}s)")
            return PhaseResult()

        added = sum(1 for c in changes if c.get("action") == "add")
        removed = sum(1 for c in changes if c.get("action") == "remove")
        log.success(
            f"sync_leanok: +{added} / -{removed} \\leanok markers ({secs}s)"
        )

        # Best-effort commit; commit_phase is a no-op if there are no
        # actual file changes (e.g. the script reported a noop in
        # verbose mode).
        commit_phase(
            ctx.project_path,
            iter_num=ctx.iter_num,
            phase="marker-sync",
            summary=f"+{added} -{removed} \\leanok ({secs}s)",
        )
        return PhaseResult()
