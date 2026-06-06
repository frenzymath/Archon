"""SyncLeanokPhase — deterministic blueprint marker sync.

Runs after the prover phase and before the review phase.
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

The phase also writes ``{state_dir}/sync_leanok-state.json``: a small
record of the last successful sync (iter, SHA, timestamp, change
counts, chapters touched). The review prompt surfaces this file so
read-only checkers can distinguish "this chapter's marker is stale,
sync will strip it" from "genuine headline-laundering" — the failure
mode the user flagged in iter-162/163 when sync_leanok had a
keyword-prefix bug that left sorry-bodied decls falsely marked.
"""

from __future__ import annotations

import datetime as _dt
import json
import subprocess
import sys
import time
from pathlib import Path

from archon import log
from archon.commands.tooling.iteration import commit_phase
from archon.commands.tooling.inner_git import InnerGit

from .base import Phase, PhaseResult


def _write_state(
    state_dir: Path,
    *,
    iter_num: int,
    project_path: Path,
    added: int,
    removed: int,
    chapters_touched: list[str],
    secs: int,
) -> None:
    """Stamp the per-iter sync result so reviewers can attribute markers.

    Best-effort; failure is silent (the sync itself already succeeded).
    """
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        sha: str | None = None
        try:
            git = InnerGit(project_path)
            if git.is_initialized():
                sha = git.head_sha(short=True)
        except Exception:
            sha = None
        payload = {
            "iter": iter_num,
            "sha": sha,
            "timestamp": _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "added": added,
            "removed": removed,
            "duration_secs": secs,
            "chapters_touched": sorted(set(chapters_touched)),
        }
        (state_dir / "sync_leanok-state.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8",
        )
    except OSError:
        pass


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

    # Outer wall-clock budget for the whole sync (the script parallelises
    # its compile-check sweep internally). Generous default so large
    # blueprints don't trip it; override via loop.sync_leanok_timeout_sec.
    _DEFAULT_TIMEOUT_SEC = 1800

    def _resolve_timeout_sec(self, ctx) -> int:
        """Resolve the sync timeout: ``loop.sync_leanok_timeout_sec`` > default."""
        try:
            from archon.commands.tooling.project_config import load_project_config

            cfg = load_project_config(ctx.project_path)
            val = cfg.loop_section().get('sync_leanok_timeout_sec')
            if val is not None:
                return max(60, int(val))
        except (OSError, ValueError, TypeError, KeyError):
            pass
        return self._DEFAULT_TIMEOUT_SEC

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
        # human-readable output. The script parallelises its per-file
        # compile-check sweep internally; the cap below is the outer
        # wall-clock budget (configurable for very large blueprints via
        # ``loop.sync_leanok_timeout_sec``).
        timeout_sec = self._resolve_timeout_sec(ctx)
        start = time.monotonic()
        try:
            r = subprocess.run(
                [sys.executable, str(script), str(ctx.project_path),
                 "--format=json"],
                capture_output=True, text=True, timeout=timeout_sec,
            )
        except (OSError, subprocess.SubprocessError) as e:
            log.warn(
                f"sync_leanok failed to run: {e} "
                f"(timeout was {timeout_sec}s — raise it with "
                f"loop.sync_leanok_timeout_sec if this is a very large project)"
            )
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

        if not isinstance(changes, list):
            changes = []

        added = sum(1 for c in changes if c.get("action") == "add")
        removed = sum(1 for c in changes if c.get("action") == "remove")
        chapters_touched = [
            c.get("chapter") for c in changes
            if c.get("action") in ("add", "remove") and c.get("chapter")
        ]

        # Always stamp the state file — even when 0 changes — so the
        # review prompt can confirm "sync ran for iter N" rather than
        # leaving reviewers to defensively flag any \leanok they see.
        _write_state(
            ctx.state_dir,
            iter_num=ctx.iter_num,
            project_path=ctx.project_path,
            added=added,
            removed=removed,
            chapters_touched=chapters_touched,
            secs=secs,
        )

        if not changes:
            log.success(f"sync_leanok: no marker changes ({secs}s)")
            return PhaseResult()

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
