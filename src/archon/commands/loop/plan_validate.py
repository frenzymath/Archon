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
— when the planner correctly skips provers for a MECHANICAL hard gate
(no ready sorries, every objective blocked by a failed upstream build,
blueprint-completeness gate failed) and writes the marker, validate
returns True, no corrective hint fires, and the iter completes cleanly.
A skip is NOT legitimate just because a strategy decision is pending:
per the plan prompt, the planner decides such forks itself and still
dispatches provers — it never idles an iter waiting on the user.

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

from .blocked_deps import (
    build_local_import_graph,
    filter_objectives_for_blocked_deps,
    parse_blocked_files_from_log,
)
from .context import LoopContext
from .sorry_count import filter_noop_objectives


# A line inside `## Current Objectives` matching this regex marks the
# iteration as an intentional no-prover round for a MECHANICAL hard gate
# (no ready sorries, all objectives blocked by a failed upstream build,
# blueprint-completeness gate failed). The validator treats this as a
# legitimate state — not a parse failure. A pending user decision is NOT
# a valid reason to skip: the planner decides strategy forks itself.
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

# The no-op filter (existing objective file with zero open sorries → a
# prover that quits immediately with no work) lives in
# ``sorry_count.filter_noop_objectives`` so the prover runner can enforce
# it too. plan_validate runs it here to warn + hint the planner; the
# runner runs it again at dispatch time (mirrors the blocked-deps split).


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
        # Drop objectives whose transitive local imports failed the
        # previous lake build — prover dispatch on them would fail to
        # load the file. The filter exempts blocked files that are
        # themselves in the objective list (presumed-being-fixed).
        original_proposed = len(objectives)
        if getattr(ctx.options, "block_on_blocked_deps", True):
            objectives, blocked_dropped = _apply_blocked_deps_filter(
                ctx, objectives,
            )
        else:
            blocked_dropped = []

        if blocked_dropped:
            blocked_meta = [
                {
                    "file": _rel_to_project(p, ctx.project_path),
                    "blockedDeps": [str(b) for b in deps],
                }
                for p, deps in blocked_dropped
            ]
            log.warn(
                f"plan-validate: dropped {len(blocked_dropped)} "
                f"objective(s) whose transitive imports failed the "
                f"previous lake build — prover dispatch on them would "
                f"fail to load the file. See planValidate."
                f"objectivesBlocked in meta.json for details."
            )
            _append_blocked_hint(
                ctx.state_dir / "USER_HINTS.md", blocked_meta,
            )

        if not objectives:
            # Every objective was filtered out as blocked-by-deps. Don't
            # silently fall through to the "no parseable objectives"
            # path — that error message would be misleading. Emit a
            # specific failure: prover phase is skipped, next iter's
            # plan agent sees the dropped list and must fix the
            # upstream blockers first.
            log.error(
                "plan-validate: every objective was dropped because its "
                "transitive imports failed the previous lake build. "
                "Skipping prover this iter; next iter's plan agent "
                "must address the upstream compile errors first."
            )
            write_meta(ctx.iter_meta, **{
                "planValidate.status": "failed_all_blocked",
                "planValidate.objectivesProposed": original_proposed,
                "planValidate.objectivesDispatched": 0,
                "planValidate.objectivesBlocked": blocked_meta,
            })
            return False

        # No-op filter — drop objectives naming an existing .lean file
        # with zero open sorries (already done / off-limits / reference),
        # which would dispatch a prover that quits immediately. Scaffold
        # dispatches and new files are exempt (see filter_noop_objectives).
        noop_dropped: list[Path] = []
        if getattr(ctx.options, "filter_noop_objectives", True):
            objectives, noop_dropped = filter_noop_objectives(
                objectives, progress_file=ctx.progress_file,
            )
        noop_rels = [_rel_to_project(p, ctx.project_path) for p in noop_dropped]
        if noop_dropped:
            log.warn(
                f"plan-validate: dropped {len(noop_dropped)} objective(s) "
                f"that name an existing .lean file with zero open sorries — "
                f"a prover on them would quit immediately with no work. See "
                f"planValidate.objectivesNoop in meta.json for details."
            )
            _append_noop_hint(ctx.state_dir / "USER_HINTS.md", noop_rels)

        if not objectives:
            # Every surviving objective was a no-op. Skip prover rather
            # than fan out lanes that all quit empty (the reported
            # "all 10 provers quit without doing anything" failure).
            log.error(
                "plan-validate: every objective was dropped as a no-op "
                "(existing file with zero open sorries). Skipping prover "
                "this iter; next iter's plan agent must list files that "
                "actually have sorries to fill, or scaffold new ones."
            )
            write_meta(ctx.iter_meta, **{
                "planValidate.status": "failed_all_noop",
                "planValidate.objectivesProposed": original_proposed,
                "planValidate.objectivesDispatched": 0,
                "planValidate.objectivesNoop": noop_rels,
                **({"planValidate.objectivesBlocked": blocked_meta}
                   if blocked_dropped else {}),
            })
            return False

        cap = ctx.options.max_objectives
        proposed = len(objectives)
        filtered_meta_field = {
            **({"planValidate.objectivesBlocked": blocked_meta}
               if blocked_dropped else {}),
            **({"planValidate.objectivesNoop": noop_rels}
               if noop_dropped else {}),
        }
        if proposed > cap:
            deferred = objectives[cap:]
            deferred_rels = [_rel_to_project(p, ctx.project_path) for p in deferred]
            log.warn(
                f"plan-validate: PROGRESS.md lists {proposed} objectives — "
                f"over the dispatch cap of {cap}. The runner will dispatch "
                f"only the first {cap}; {len(deferred)} files are deferred "
                f"and re-surfaced to the next plan agent via USER_HINTS. "
                f"This guards against runaway fan-out (e.g. 27 provers "
                f"launched in one iter)."
            )
            _append_overcap_hint(
                ctx.state_dir / "USER_HINTS.md",
                cap=cap, proposed=proposed, deferred_rels=deferred_rels,
            )
            write_meta(ctx.iter_meta, **{
                "planValidate.status": "ok_overcap",
                "planValidate.objectivesProposed": proposed,
                "planValidate.objectivesDispatched": cap,
                "planValidate.objectivesDeferred": deferred_rels,
                **filtered_meta_field,
            })
            return True
        write_meta(ctx.iter_meta, **{
            "planValidate.status": "ok",
            "planValidate.objectives": proposed,
            **filtered_meta_field,
        })
        return True

    # No parseable objectives — check for the intentional-skip marker
    # before treating this as a parse failure.
    if _has_intentional_skip_marker(ctx.progress_file):
        log.info(
            "plan-validate: PROGRESS.md flagged as intentional no-prover "
            "this iter (mechanical hard gate). Proceeding "
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


def _apply_blocked_deps_filter(
    ctx: LoopContext,
    objectives: list[Path],
) -> tuple[list[Path], list[tuple[Path, list[Path]]]]:
    """Filter objectives whose transitive imports failed the last lake build.

    Returns ``(kept, dropped)``. ``dropped`` is
    ``[(objective_path, [blocked_dep_rel, ...]), ...]`` — the second
    element lists the blocked files that disqualified the objective,
    so the caller can render a useful USER_HINTS message.

    The blocked set is read from ``.archon/last_lake_build.log`` — the
    file the finalize phase writes when ``lake build`` fails. An
    empty / missing log returns ``(objectives, [])`` (nothing to
    filter against).
    """
    log_path = ctx.state_dir / "last_lake_build.log"
    blocked = parse_blocked_files_from_log(
        log_path, project_path=ctx.project_path,
    )
    if not blocked:
        return objectives, []
    graph = build_local_import_graph(ctx.project_path)
    return filter_objectives_for_blocked_deps(
        objectives,
        blocked=blocked,
        graph=graph,
        project_path=ctx.project_path,
    )


def _append_noop_hint(hints_file: Path, noop_rels: list[str]) -> None:
    """Append a hint listing files dropped as guaranteed no-op dispatches.

    The next plan agent reads-then-clears USER_HINTS, so this lands in
    front of the planner exactly once. It tells the planner these files
    had nothing to prove — they were already done, or listed as
    off-limits/reference, and should not be re-listed as objectives.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    listing = "\n".join(f"  - {r}" for r in noop_rels) or "  - (none)"
    hint = (
        f"\n- [{ts}] archon[plan-validate]: dropped {len(noop_rels)} "
        f"objective(s) that name an existing `.lean` file with ZERO open "
        f"sorries — a prover on them would quit immediately with no work. "
        f"Do not re-list a done/off-limits/reference-only file under "
        f"`## Current Objectives`; if you meant to scaffold new "
        f"declarations into it, say so explicitly (\"scaffold …\"). Files:\n"
        f"{listing}\n"
    )
    hints_file.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if hints_file.exists():
        try:
            existing = hints_file.read_text()
        except OSError:
            pass
    hints_file.write_text(existing + hint)


def _append_blocked_hint(
    hints_file: Path,
    blocked_meta: list[dict],
) -> None:
    """Append a hint listing files dropped because their imports don't compile.

    The next plan agent reads-then-clears USER_HINTS, so this lands
    in front of the planner exactly once. Listing the specific
    blocking deps means the planner can prioritize fixing the
    upstream files first.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f"\n- [{ts}] archon[plan-validate]: dropped "
        f"{len(blocked_meta)} objective(s) because their transitive "
        f"imports failed the previous `lake build`. Fix the blocking "
        f"deps first, then re-list the downstream files:",
    ]
    for entry in blocked_meta:
        deps = ", ".join(entry["blockedDeps"]) or "(none)"
        lines.append(f"  - {entry['file']} — blocked by: {deps}")
    hint = "\n".join(lines) + "\n"
    hints_file.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if hints_file.exists():
        try:
            existing = hints_file.read_text()
        except OSError:
            pass
    hints_file.write_text(existing + hint)


def _rel_to_project(path: Path, project_path: Path) -> str:
    """Render a parsed-objective Path as a project-relative string."""
    try:
        return str(path.resolve().relative_to(project_path.resolve()))
    except ValueError:
        return str(path)


def _append_overcap_hint(
    hints_file: Path,
    *,
    cap: int,
    proposed: int,
    deferred_rels: list[str],
) -> None:
    """Append a hint listing the files deferred by the dispatch cap.

    The next plan agent reads-then-clears USER_HINTS each iter, so the
    listing lands in front of the planner exactly once. Including the
    file paths means the planner doesn't have to dig through meta.json
    to re-prioritize.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    deferred_block = "\n".join(f"  - {r}" for r in deferred_rels) or "  - (none)"
    hint = (
        f"\n- [{ts}] archon[plan-validate]: previous iter's PROGRESS.md "
        f"listed {proposed} objectives — over the dispatch cap of {cap}. "
        f"The first {cap} were dispatched; the {len(deferred_rels)} below "
        f"were deferred. Re-prioritize: pick the most urgent for this "
        f"iter (still within the cap), defer or drop the rest.\n"
        f"{deferred_block}\n"
    )
    hints_file.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if hints_file.exists():
        try:
            existing = hints_file.read_text()
        except OSError:
            pass
    hints_file.write_text(existing + hint)


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
