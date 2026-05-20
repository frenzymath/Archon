"""Typer-decorated `loop` entry point.

Kept thin: parse CLI flags, resolve them against `.archon/config.json`,
build a `LoopOptions`, and call `LoopCommand.run()`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from archon.agent import DEFAULT_MODEL
from archon.types import Stage

from .command import LoopCommand, parse_from_phase
from .context import LoopOptions


def loop(
    project_path: str = typer.Argument(".", help="Path to Lean project"),
    max_iterations: Optional[int] = typer.Option(
        None, "--max-iterations", "-m",
        help="Max plan→prover→review cycles. (default from .archon/config.json or 10)",
    ),
    max_parallel: Optional[int] = typer.Option(
        None, "--max-parallel",
        help="Max concurrent provers in parallel mode. (default from config or 4)",
    ),
    max_objectives: Optional[int] = typer.Option(
        None, "--max-objectives",
        help="Hard cap on the number of files the planner can list under "
             "'## Current Objectives' for a single iter. When the planner "
             "exceeds it (e.g. writes 27 files), only the first N are "
             "dispatched to provers; the remainder are deferred to the "
             "next iter via a USER_HINTS entry. Prevents the runaway "
             "fan-out failure mode. (default from config or 10)",
    ),
    block_on_blocked_deps: Optional[bool] = typer.Option(
        None, "--block-on-deps/--no-block-on-deps",
        help="Skip prover dispatch for an objective when one of its "
             "transitive local imports failed the previous `lake build`. "
             "Such provers would fail to load the file anyway. The "
             "blocked-and-itself-objective case (planner is fixing the "
             "blocked file) is exempt. (default from config or on)",
    ),
    stage: Optional[Stage] = typer.Option(
        None, "--stage", "-s",
        help="Force a stage instead of reading from PROGRESS.md.",
    ),
    parallel: Optional[bool] = typer.Option(
        None, "--parallel/--serial",
        help="Run provers in parallel (one per file) or serially. (default from config or parallel)",
    ),
    verbose_logs: Optional[bool] = typer.Option(
        None, "--verbose-logs/--no-verbose-logs",
        help="Save raw Claude stream events to .raw.jsonl. (default from config or off)",
    ),
    no_review: Optional[bool] = typer.Option(
        None, "--no-review/--review",
        help="Skip review phase after each iteration. (default from config or off)",
    ),
    no_finalize: bool = typer.Option(
        False, "--no-finalize",
        help="Skip the end-of-iteration git commit / lake build / blueprint web.",
    ),
    no_git_commit: bool = typer.Option(
        False, "--no-git-commit",
        help="Skip only the per-iteration git commit (keeps lake/blueprint).",
    ),
    no_lake_build: bool = typer.Option(
        False, "--no-lake-build",
        help="Skip only the per-iteration `lake build`.",
    ),
    no_blueprint_web: bool = typer.Option(
        False, "--no-blueprint-web",
        help="Skip only the per-iteration `leanblueprint web`.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Print prompts without launching Claude.",
    ),
    no_dashboard: bool = typer.Option(
        False, "--no-dashboard",
        help="Do not auto-start the web dashboard.",
    ),
    blueprint_server_flag: bool = typer.Option(
        False, "--blueprint-server",
        help="Start a local HTTP server serving blueprint/web/ alongside the dashboard.",
    ),
    open_browser: bool = typer.Option(
        False, "--open",
        help="Open the dashboard in a browser as soon as it starts.",
    ),
    model: Optional[str] = typer.Option(
        None, "--model", "-M",
        help="Model used for every plan / prover / review phase in the loop. "
             "Anthropic aliases: 'opus', 'sonnet', 'haiku' or a full id. "
             "Non-Anthropic providers (require credentials in .archon/.env): "
             "'kimi', 'deepseek'. "
             "(default from .archon/config.json or 'opus')",
    ),
    from_phase: Optional[str] = typer.Option(
        None, "--from",
        help="Skip earlier phases on the FIRST iteration so you can resume "
             "after stopping mid-iteration. Choices: plan, prover, "
             "review. E.g. '--from prover' keeps the existing PROGRESS.md, "
             "skips plan, and starts the first iteration at the prover phase. "
             "Subsequent iterations run the full sequence as usual. "
             "'--from plan' reuses the previous iter-NNN log dir instead of "
             "bumping to a new iteration number — useful for retrying a "
             "crashed iteration in place.",
    ),
    resume: bool = typer.Option(
        False, "--resume",
        help="Resume the previous iteration's Claude session. Without "
             "--from, auto-detects which phase to resume by scanning the "
             "prior iter's meta.json for the first plan/prover/review whose "
             "status isn't 'done' (i.e. the one that crashed). With "
             "--from <phase>, resumes that specific phase. Reuses the prior "
             "iter-NNN dir, looks up the stored session id, and invokes "
             "claude with --resume <id> plus a short 'continue from where "
             "you left off' prompt instead of re-priming with the full "
             "phase prompt. Falls back to a fresh run when no session id is "
             "stored. Only affects the FIRST iteration; subsequent "
             "iterations run plan/prover/review fresh.",
    ),
    debug_feedback: Optional[bool] = typer.Option(
        None, "--debug-feedback/--no-debug-feedback",
        help="Open a write-only feedback channel: agents and subagents may "
             "append short notes to .archon/.debug-feedback/debug_feedback.md "
             "when they notice something the developer should fix. "
             "(default from config or off)",
    ),
) -> None:
    """Start the automated plan → prove → review loop.

    Each iteration:
      1. Plan agent (reads project state, writes objectives)
      2. Prover agent(s) — parallel or serial
      3. Review agent (unless --no-review)
      4. Finalize: git commit, lake build, leanblueprint web (non-fatal)

    By default the web dashboard is launched in the background. Pass
    --blueprint-server to also start the blueprint HTML server.
    """
    resolved = Path(project_path).resolve()

    # ── Resolve CLI options against .archon/config.json ───────────────
    # Precedence: CLI > config.json > built-in defaults. CLI options
    # default to None as a sentinel for "user didn't set this", so we
    # can distinguish an explicit --no-review from an unset one.
    from archon.commands.tooling.env_loader import load_env_file as _load_env
    from archon.commands.tooling.project_config import (
        load_project_config,
        resolve as _resolve,
    )

    project_config = load_project_config(resolved)
    loop_cfg = project_config.loop_section()
    multilane_cfg = project_config.multilane_section()

    # Project-local .env wins over global cwd .env (which cli.py loaded).
    _load_env(resolved)

    max_iterations = _resolve(max_iterations, section=loop_cfg, key='max_iterations', default=10)
    max_parallel = _resolve(max_parallel, section=loop_cfg, key='max_parallel', default=4)
    max_objectives = _resolve(max_objectives, section=loop_cfg, key='max_objectives', default=10)
    block_on_blocked_deps = _resolve(
        block_on_blocked_deps, section=loop_cfg, key='block_on_blocked_deps', default=True,
    )
    parallel = _resolve(parallel, section=loop_cfg, key='parallel', default=True)
    verbose_logs = _resolve(verbose_logs, section=loop_cfg, key='verbose_logs', default=False)
    no_review = _resolve(no_review, section=loop_cfg, key='no_review', default=False)
    model = _resolve(model, section=loop_cfg, key='model', default=DEFAULT_MODEL)
    debug_feedback = _resolve(
        debug_feedback, section=loop_cfg, key='debug_feedback', default=False,
    )

    # Multi-lane execution fires automatically when config.json has it
    # enabled with at least one lane defined. The old --multilane-execute
    # / --multilane-preview flags are gone — the user controls this
    # purely through .archon/config.json.
    multilane_lanes = multilane_cfg.get('lanes') or []
    multilane_execute = bool(multilane_cfg.get('enabled')) and len(multilane_lanes) >= 1

    # --resume without an explicit --from auto-detects which phase to
    # resume by inspecting the prior iter's meta.json — done inside
    # LoopCommand once the iter dir is known. Leave from_phase=None
    # here so the resolved target stays a context concern.
    options = LoopOptions(
        project_path=resolved,
        max_iterations=max_iterations,
        max_parallel=max_parallel,
        max_objectives=max_objectives,
        block_on_blocked_deps=block_on_blocked_deps,
        parallel=parallel,
        verbose_logs=verbose_logs,
        no_review=no_review,
        no_finalize=no_finalize,
        no_git_commit=no_git_commit,
        no_lake_build=no_lake_build,
        no_blueprint_web=no_blueprint_web,
        dry_run=dry_run,
        no_dashboard=no_dashboard,
        blueprint_server_flag=blueprint_server_flag,
        open_browser=open_browser,
        model=model,
        force_stage=stage.value if stage else None,
        skip_first_iter=parse_from_phase(from_phase),
        from_phase=from_phase,
        multilane_execute=multilane_execute,
        multilane_preview=False,  # legacy; kept False so existing dispatch falls through
        multilane_cfg=multilane_cfg,
        debug_feedback=debug_feedback,
        resume=resume,
    )

    LoopCommand(options).run()
