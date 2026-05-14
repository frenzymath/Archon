"""Post-plan validation step.

Runs between :class:`PlanPhase` and :class:`ProverPhase`. Calls
:func:`state.auto_fix_objectives` to (1) verify the plan agent produced
a PROGRESS.md that the prover dispatcher can actually parse, and (2)
silently rename common heading drift (``## Strategy`` →
``## Current Objectives``) so a productive plan isn't wasted by a
one-character format mistake. On persistent failure, appends a
corrective hint to ``USER_HINTS.md`` (which the next plan agent reads
and clears) and signals the caller to skip prover dispatch for this
iteration — avoiding the multi-hour silent-loop failure where the plan
keeps producing un-parseable objectives and review misdiagnoses it as a
mathematical dead end.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from archon import log
from archon.commands.tooling.iteration import commit_phase
from archon.state import auto_fix_objectives, write_meta

from .context import LoopContext


_HINT_HEADER = "## archon[plan-validate] feedback"


def validate_plan_output(ctx: LoopContext) -> bool:
    """Return True if PROGRESS.md has parseable objectives.

    Side effects on True with rewrites: PROGRESS.md is updated in place
    and an inner-git commit ``archon[NNN/plan-fixup]`` records what
    changed. Side effects on False: a corrective hint is appended to
    ``USER_HINTS.md`` and ``planValidate.status=failed`` is stamped
    into the iteration's ``meta.json``.

    Returns True unchanged when the plan phase was skipped via
    ``--from``: the user explicitly trusted the existing PROGRESS.md;
    the validator shouldn't second-guess hand-edited state.
    """
    if ctx.dry_run:
        return True
    if "plan" in ctx.skip_now:
        return True
    if ctx.iter_meta is None:
        # No meta to record into — bail safely.
        return True

    log.step("Validating plan output…")
    objectives, fixes = auto_fix_objectives(
        ctx.progress_file, ctx.project_path,
    )

    if fixes:
        for fix in fixes:
            log.info(f"plan-validate: {fix}")
        commit_phase(
            ctx.project_path, iter_num=ctx.iter_num, phase="plan-fixup",
            summary=f"auto-fix: {', '.join(fixes)}",
        )
        write_meta(ctx.iter_meta, **{"planValidate.fixes": fixes})

    if objectives:
        write_meta(ctx.iter_meta, **{
            "planValidate.status": "ok",
            "planValidate.objectives": len(objectives),
        })
        return True

    log.error(
        "plan-validate: PROGRESS.md has no parseable objectives under "
        "'## Current Objectives' (after auto-fix). Skipping prover this "
        "iteration; appended a corrective hint to USER_HINTS.md so the "
        "next plan agent can self-correct."
    )
    _append_hint(ctx.state_dir / "USER_HINTS.md")
    write_meta(ctx.iter_meta, **{
        "planValidate.status": "failed",
        "planValidate.objectives": 0,
    })
    return False


def _append_hint(hints_file: Path) -> None:
    """Append a one-shot corrective hint for the next plan agent.

    Appended (not clobbered) because the user may have written their own
    hints into this file too; the plan agent reads-then-clears the whole
    file each iteration, so our hint will be consumed exactly once.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    hint = (
        f"\n\n{_HINT_HEADER} ({ts})\n"
        "The previous iteration's PROGRESS.md write produced no parseable "
        "objectives, so no prover ran and no work was done.\n\n"
        "The prover dispatcher requires:\n"
        "  - A section literally headed `## Current Objectives`\n"
        "  - Inside it, `### N. **`File.lean`**` entries (one per assigned file)\n\n"
        "Do NOT put objectives under `## Strategy`, `## Plan`, `## Objectives`, "
        "or any other heading — only `## Current Objectives` is parsed. "
        "Rewrite PROGRESS.md now with the correct heading.\n"
    )
    hints_file.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if hints_file.exists():
        try:
            existing = hints_file.read_text()
        except OSError:
            pass
    hints_file.write_text(existing + hint)
