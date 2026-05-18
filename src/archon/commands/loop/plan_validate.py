"""Post-plan validation step.

Runs between :class:`PlanPhase` and :class:`ProverPhase`. Calls
:func:`state.auto_fix_objectives` to (1) verify the plan agent produced
a PROGRESS.md that the prover dispatcher can actually parse, and (2)
silently rename common heading drift (``## Strategy`` →
``## Current Objectives``) so a productive plan isn't wasted by a
one-character format mistake. On persistent failure, appends a
*discuss-format* corrective line to ``USER_HINTS.md`` (which the next
plan agent reads and clears) and signals the caller to skip prover
dispatch for this iteration.

Recognizes an *intentional* no-prover-this-iter marker in PROGRESS.md
— when the planner correctly skips provers (user escalation, hard
gate, etc.) and writes the marker, validate returns True, no
corrective hint fires, and the iter completes cleanly.

Also enforces soft length / structure limits on STRATEGY.md — see
:func:`_check_strategy_bounds`. STRATEGY.md has been observed bloating
to multi-thousand-line dumps with verbatim Lean source content; the
soft check logs a warning and stamps ``planValidate.strategyBloat=
true`` in meta.json so the dashboard / next iter can surface it.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from archon import log
from archon.commands.tooling.iteration import commit_phase
from archon.state import auto_fix_objectives, write_meta
from archon.state.progress import _extract_section

from .context import LoopContext


# A line inside `## Current Objectives` matching this regex marks the
# iteration as an intentional no-prover round (e.g. user escalation,
# hard gate fired, blueprint-completeness gate failed). The validator
# treats this as a legitimate state — not a parse failure.
_INTENTIONAL_SKIP_RE = re.compile(
    r"\(no\s+prover\s+(dispatch\s+)?this\s+iter",
    re.IGNORECASE,
)


# Soft ceilings for STRATEGY.md. The canonical schema is documented in
# `.archon-src/prompts/plan.md` § "Long-arc Strategy" — keep the file
# under ~250 lines / ~12 KB. The validator's hard warning threshold is
# higher (400 lines / 20 KB) to allow some breathing room for projects
# with many live routes, but consistently bumping into this ceiling is
# a signal the planner is using STRATEGY.md as a scratchpad.
_STRATEGY_LINE_CEILING = 400
_STRATEGY_BYTES_CEILING = 20 * 1024

# Detects code-fenced or `\begin{theorem}` blocks — STRATEGY.md is
# never supposed to contain inline Lean or blueprint source. Catching
# these here forces the planner to move them to the blueprint chapter
# or iter sidecar where they belong.
_LEAN_FENCE_RE = re.compile(r"^```\s*lean\b", re.IGNORECASE | re.MULTILINE)
_TEX_THM_RE = re.compile(r"\\begin\{(theorem|lemma|proof|definition)\}")


def validate_plan_output(ctx: LoopContext) -> bool:
    """Return True if PROGRESS.md has parseable objectives OR an
    intentional-skip marker.

    Side effects:

    * On rewrites: PROGRESS.md is updated in place and an inner-git
      commit ``archon[NNN/plan-fixup]`` records what changed.
    * On parse failure with no intentional-skip marker: a discuss-format
      corrective hint is appended to ``USER_HINTS.md`` and
      ``planValidate.status=failed`` is stamped into the iteration's
      ``meta.json``.
    * On intentional skip: ``planValidate.status=ok_intentional_skip``
      and the iter proceeds with 0 prover dispatches.

    Returns True unchanged when the plan phase was skipped via
    ``--from``: the user explicitly trusted the existing PROGRESS.md.
    """
    if ctx.dry_run:
        return True
    if "plan" in ctx.skip_now:
        return True
    if ctx.iter_meta is None:
        return True

    log.step("Validating plan output…")
    _check_strategy_bounds(ctx)
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

    # No parseable objectives — check for the intentional-skip marker
    # before treating this as a parse failure.
    if _has_intentional_skip_marker(ctx.progress_file):
        log.info(
            "plan-validate: PROGRESS.md flagged as intentional no-prover "
            "this iter (e.g. user escalation, hard gate). Proceeding "
            "without prover dispatch; no corrective hint appended."
        )
        write_meta(ctx.iter_meta, **{
            "planValidate.status": "ok_intentional_skip",
            "planValidate.objectives": 0,
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


def _check_strategy_bounds(ctx: LoopContext) -> None:
    """Soft-warn when STRATEGY.md has bloated past its canonical bounds.

    The plan-prompt's "Long-arc Strategy" section defines a fixed schema
    and length ceilings (~250 lines, ~12 KB working; 400 lines, 20 KB
    hard-warn). We don't gate the iter on this — too disruptive — but we
    log a warning and stamp ``planValidate.strategyBloat=true`` so the
    next iter's plan agent (and the dashboard) can see it.

    Also flags inline Lean code fences or LaTeX theorem environments
    inside STRATEGY.md — both are explicit "no Lean dumps" violations
    documented in the canonical schema.
    """
    strategy_file = ctx.state_dir / "STRATEGY.md"
    if not strategy_file.exists():
        return
    try:
        text = strategy_file.read_text(encoding="utf-8")
    except OSError:
        return

    line_count = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    byte_count = len(text.encode("utf-8"))
    lean_fences = len(_LEAN_FENCE_RE.findall(text))
    tex_envs = len(_TEX_THM_RE.findall(text))

    too_long = (
        line_count > _STRATEGY_LINE_CEILING
        or byte_count > _STRATEGY_BYTES_CEILING
    )
    has_forbidden_content = lean_fences > 0 or tex_envs > 0

    if not too_long and not has_forbidden_content:
        return

    pieces: list[str] = []
    if too_long:
        pieces.append(
            f"{line_count} lines / {byte_count // 1024} KB "
            f"(ceiling: {_STRATEGY_LINE_CEILING} lines / "
            f"{_STRATEGY_BYTES_CEILING // 1024} KB)"
        )
    if lean_fences:
        pieces.append(f"{lean_fences} ```lean fence(s)")
    if tex_envs:
        pieces.append(f"{tex_envs} \\begin{{theorem|lemma|proof|definition}} env(s)")

    log.warn(
        "plan-validate: STRATEGY.md bloat detected — "
        + "; ".join(pieces)
        + ". The canonical schema (see prompts/plan.md § \"Long-arc "
          "Strategy\") forbids inline Lean / blueprint content and caps "
          "the file at ~250 lines. Trim per-phase rows that have "
          "completed and move any Lean code to the blueprint chapter."
    )
    if ctx.iter_meta is not None:
        write_meta(ctx.iter_meta, **{
            "planValidate.strategyBloat": True,
            "planValidate.strategyLines": line_count,
            "planValidate.strategyBytes": byte_count,
            "planValidate.strategyLeanFences": lean_fences,
            "planValidate.strategyTexEnvs": tex_envs,
        })


def _has_intentional_skip_marker(progress_file: Path) -> bool:
    """True iff PROGRESS.md's ``## Current Objectives`` section contains
    a recognized "intentional no-prover this iter" marker line.
    """
    if not progress_file.exists():
        return False
    try:
        text = progress_file.read_text()
    except OSError:
        return False
    section_lines = _extract_section(text, "## Current Objectives")
    for line in section_lines:
        if _INTENTIONAL_SKIP_RE.search(line):
            return True
    return False


def _append_hint(hints_file: Path) -> None:
    """Append a one-line discuss-format corrective hint.

    Discuss-format keeps USER_HINTS.md as a uniform list of timestamped
    one-liners (the ``archon discuss`` convention). The plan agent
    reads-then-clears the file each iteration so the hint is consumed
    exactly once.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    hint = (
        f"\n- [{ts}] archon[plan-validate]: previous iter's PROGRESS.md "
        "had no parseable objectives under `## Current Objectives` and "
        "no `(no prover dispatch this iter ...)` skip marker. Rewrite "
        "with the canonical heading + `### N. **`File.lean`**` entries, "
        "or (if intentional) add the skip marker line.\n"
    )
    hints_file.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if hints_file.exists():
        try:
            existing = hints_file.read_text()
        except OSError:
            pass
    hints_file.write_text(existing + hint)
