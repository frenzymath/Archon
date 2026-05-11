"""One-off maintenance commands for legacy Archon projects.

Currently only ``archon migrate rename-sessions``: rename the
pre-2026 monotonic ``proof-journal/sessions/session_N/`` directories
to match their corresponding iteration number, so they line up with
the post-2026 ``session_<iter>`` convention. Idempotent and safe to
re-run — already-correct sessions are skipped.
"""

from __future__ import annotations

from pathlib import Path

import typer

from archon import log
from archon.state import (
    SessionRename,
    apply_session_renames,
    cleanup_empty_sessions,
    plan_session_renames,
)


app = typer.Typer(
    name="migrate",
    help="One-off migrations for legacy Archon projects.",
    no_args_is_help=True,
)


def _resolve_state_dir(project_path: str) -> tuple[Path, Path]:
    resolved = Path(project_path).resolve()
    state_dir = resolved / ".archon"
    if not state_dir.is_dir():
        log.error(f"Not an Archon project: {resolved}")
        raise typer.Exit(1)
    return resolved, state_dir


@app.command("rename-sessions")
def rename_sessions(
    project_path: str = typer.Argument(".", help="Path to Archon project"),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Print the rename plan; do not move anything.",
    ),
) -> None:
    """Rename ``session_N/`` to ``session_<iter>/`` so directory names
    match their iteration.

    The matching is done by mtime: each session dir's content
    timestamp is compared to the inner-git ``archon[NNN/review]``
    commit times, and the closest match within an hour wins. Sessions
    that don't fall within an hour of any review commit (typically
    leftovers from review runs whose commit was later
    ``git reset --hard``-ed away) are reported as orphans and left
    untouched.

    Empty session directories (no content / only zero-byte files)
    are also swept here as a side effect — they correspond to
    crashed/interrupted review runs.
    """
    resolved, state_dir = _resolve_state_dir(project_path)

    log.header("archon migrate rename-sessions")

    # Step 1: clean up empty stub dirs first (so they don't show up
    # in the plan as orphans).
    if not dry_run:
        removed = cleanup_empty_sessions(state_dir)
        if removed:
            log.success(f"Removed {removed} empty session director(y/ies)")
    else:
        # In dry-run, just report what we'd remove.
        sessions_dir = state_dir / "proof-journal" / "sessions"
        empty = []
        if sessions_dir.is_dir():
            for d in sessions_dir.iterdir():
                if not d.is_dir() or not d.name.startswith("session_"):
                    continue
                try:
                    files = list(d.iterdir())
                except OSError:
                    continue
                if not files or all(
                    f.is_file() and f.stat().st_size == 0 for f in files
                ):
                    empty.append(d.name)
        if empty:
            log.info(f"[dry-run] Would remove {len(empty)} empty dir(s): "
                     f"{', '.join(empty)}")

    # Step 2: build + display the rename plan.
    planned, orphans = plan_session_renames(state_dir, resolved)

    if not planned and not orphans:
        log.success("Nothing to do — all sessions already match their iteration.")
        return

    if planned:
        log.info(f"{len(planned)} session(s) will be renamed:")
        for r in planned:
            log.step(
                f"  {r.old:<14} -> {r.new}  (Δ={r.delta_secs}s from "
                f"archon[{r.iter_num:03d}/review])"
            )
    else:
        log.info("No renames needed — every kept session is already correctly named.")

    if orphans:
        log.warn(
            f"{len(orphans)} session(s) couldn't be matched to a review "
            f"commit and will be left alone:"
        )
        for o in orphans:
            log.step(f"  {o}")
        log.step(
            "These usually correspond to review runs whose inner-git commit "
            "was later reset away. Inspect them by hand and `rm -rf` if you "
            "want them gone."
        )

    if dry_run:
        log.info("[dry-run] No files moved. Re-run without --dry-run to apply.")
        return

    if not planned:
        return

    completed = apply_session_renames(state_dir, planned)
    failed = [r for r in planned if r not in completed]
    log.success(f"Renamed {len(completed)} session director(y/ies)")
    if failed:
        log.warn(
            f"{len(failed)} rename(s) failed (target name was already taken): "
            + ", ".join(f"{r.old}->{r.new}" for r in failed)
        )
