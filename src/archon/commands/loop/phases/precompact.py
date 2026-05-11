"""Pre-compaction phase: shrink oversized state files before the next agent.

There are two attachment points in the loop:

- ``PreCompactPlanPhase`` runs **before plan**, compacting STRATEGY.md,
  task_pending.md, task_done.md. These three are compacted in parallel
  since each Claude call is independent and I/O-bound.
- ``PreCompactReviewPhase`` runs **before review**, compacting
  PROJECT_STATUS.md.

Each compactor is gated by a per-target threshold (see
``compaction.targets`` in ``.archon/config.json``). A skipped target is
genuinely silent — no Claude call, no log, no commit. When a compactor
does run, its rewrite is committed to the inner-git as
``archon[NNN/precompact/<role>]: <pre>->{<post>} chars`` so the user
can audit and revert.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from archon import log
from archon.commands.tooling.iteration import commit_phase
from archon.commands.tooling.project_config import (
    load_project_config,
    resolve_compaction_enabled,
)
from archon.compactors import (
    Compactor,
    ProjectStatusCompactor,
    StrategyCompactor,
    TaskDoneCompactor,
    TaskPendingCompactor,
)

from .base import Phase, PhaseResult


def _run_compactors(
    ctx,
    *,
    phase_label: str,
    skip_token: str,
    compactor_classes: list[type[Compactor]],
) -> PhaseResult:
    """Shared implementation for both attachment points.

    The two phases differ only in (a) which compactors they run, and
    (b) the skip token they respond to. Everything else — gating,
    log-path layout, commit summary, dry-run handling — is identical.

    Within a phase, compactors that need to run are executed
    concurrently in a thread pool. Each compactor writes a different
    file, so there's no write contention; the single ``commit_phase``
    at the end picks up all rewrites together.
    """
    if skip_token in ctx.skip_now:
        log.phase(0, f"{phase_label} — skipped (--from)")
        return PhaseResult(skipped=True)

    if ctx.dry_run:
        log.phase(0, f"{phase_label} — skipped (--dry-run)")
        return PhaseResult(skipped=True)

    cfg = load_project_config(ctx.project_path)
    if not resolve_compaction_enabled(cfg):
        # Compaction globally disabled — quietly skip.
        return PhaseResult(skipped=True)

    iter_log_dir: Path = ctx.iter_dir
    if iter_log_dir is None:
        return PhaseResult(skipped=True)

    log.phase(0, phase_label)

    # First pass: gate serially so skip logs stay tidy and deterministic.
    to_run: list[Compactor] = []
    for cls in compactor_classes:
        compactor = cls(ctx.project_path, verbose_logs=ctx.verbose_logs)
        ok_to_run, reason = compactor.needs_compaction()
        if not ok_to_run:
            log.info(f"{compactor.name}: skip ({reason})")
            continue
        to_run.append(compactor)

    if not to_run:
        return PhaseResult()

    # Second pass: run the survivors in parallel. Claude calls are the
    # slow part and are network-bound, so threads (not processes) are
    # the right tool — no GIL contention on the hot path.
    start = time.monotonic()
    results_by_key: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=len(to_run)) as ex:
        futures = {
            ex.submit(
                c.run,
                iter_num=ctx.iter_num,
                log_base=iter_log_dir / c.name,
            ): c
            for c in to_run
        }
        for fut in as_completed(futures):
            compactor = futures[fut]
            try:
                result = fut.result()
            except Exception as exc:
                # Don't let one bad compactor sink the others; log and
                # move on. The unchanged file will still be picked up
                # next iteration.
                log.info(f"{compactor.name}: failed ({exc!r})")
                continue
            if not result.ran:
                continue
            if result.changed:
                results_by_key[compactor.config_key] = (
                    f"{compactor.config_key}: {result.pre_size}->{result.post_size}"
                )
            else:
                results_by_key[compactor.config_key] = (
                    f"{compactor.config_key}: unchanged"
                )

    if not results_by_key:
        return PhaseResult()

    # Preserve original class ordering in the commit message so diffs
    # across iterations stay stable regardless of which thread finished
    # first.
    summary_parts = [
        results_by_key[c.config_key]
        for c in to_run
        if c.config_key in results_by_key
    ]

    secs = int(time.monotonic() - start)
    summary = ", ".join(summary_parts) or "no-op"
    # Single inner-git commit covering everything this phase rewrote.
    # Slug differs by attachment point so the dashboard groups the two
    # phases distinctly without colliding with each other or with the
    # main per-phase commits.
    commit_phase(
        ctx.project_path,
        iter_num=ctx.iter_num,
        phase="precompact",
        file_slug=skip_token,  # "plan" or "review"
        summary=f"{summary} ({secs}s)",
    )
    return PhaseResult()


class PreCompactPlanPhase(Phase):
    """Compact STRATEGY.md, task_pending.md, task_done.md before plan."""
    name = "Pre-compact (plan)"
    skip_token = "plan"

    def run(self) -> PhaseResult:
        return _run_compactors(
            self.ctx,
            phase_label=self.name,
            skip_token=self.skip_token,
            compactor_classes=[
                StrategyCompactor,
                TaskPendingCompactor,
                TaskDoneCompactor,
            ],
        )


class PreCompactReviewPhase(Phase):
    """Compact PROJECT_STATUS.md before review."""
    name = "Pre-compact (review)"
    skip_token = "review"

    def run(self) -> PhaseResult:
        return _run_compactors(
            self.ctx,
            phase_label=self.name,
            skip_token=self.skip_token,
            compactor_classes=[ProjectStatusCompactor],
        )