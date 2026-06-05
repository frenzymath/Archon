"""Typer entry point for ``archon merge``.

Combines two archon projects that share part of their blueprint DAG. The
``target`` is duplicated into ``--dest`` (never modified); the ``--from``
source is mounted read-only. For every declaration the two share — same
Lean name / blueprint label, same statement — the session keeps the better
proof (``--prefer source`` by default, since you usually merge a project
*because* it carries the improvement). A genuinely differing statement is a
conflict the session surfaces rather than auto-merges.

Because a swapped-in proof only type-checks against the *signatures* of the
lemmas it uses (not their proofs), best-per-declaration selection is a safe,
local operation — and the ``lake build`` verify gate (on by default here) is
what proves the merged result actually holds together.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from archon.agent import DEFAULT_MODEL
from archon.commands.tooling.project_config import (
    load_project_config,
    resolve_claude_backend,
)

from .command import ExtractCommand


def merge(
    target: str = typer.Argument(
        ".", help="The archon project to merge INTO (duplicated, never modified)."
    ),
    source: str = typer.Option(
        ..., "--from", "-f",
        help="The archon project to merge FROM (read-only). Must share the "
             "target's lean-toolchain and mathlib pin.",
    ),
    dest: str = typer.Option(
        ..., "--dest", "-d",
        help="Where to create the merged sandbox (must not exist).",
    ),
    union: bool = typer.Option(
        False, "--union",
        help="Full union: also import the source's own (non-shared) cones, "
             "not just upgrade the declarations the two share. Default is to "
             "keep the target's scope and only enrich the overlap.",
    ),
    prefer: str = typer.Option(
        "source", "--prefer",
        help="Which side's proof wins when both have the same declaration: "
             "'source' (default — the --from project) or 'target'. A differing "
             "statement is always surfaced as a conflict, never auto-merged.",
    ),
    lake: str = typer.Option(
        "hardlink", "--lake",
        help="How to materialize .lake in the sandbox: 'hardlink' (cp -al, "
             "instant, ~0 disk), 'copy' (full copy), 'none' (skip).",
    ),
    resume: bool = typer.Option(
        False, "--resume",
        help="Re-enter an existing sandbox (skip duplication) — e.g. after a "
             "failed verify gate.",
    ),
    build: bool = typer.Option(
        True, "--build/--no-build",
        help="Run `lake build` in the verify gate. ON by default for merge — "
             "it is the only check that the swapped-in proofs are consistent "
             "against the target's signatures. Pass --no-build to skip (faster, "
             "but you must build yourself before trusting the result).",
    ),
    model: str = typer.Option(
        DEFAULT_MODEL, "--model", "-M",
        help="Model alias for the interactive session ('opus', 'sonnet', ...).",
    ),
    claude_backend: Optional[str] = typer.Option(
        None, "--claude-backend",
        help="How `claude` is invoked (default | vscode | desktop).",
    ),
) -> None:
    """Merge two archon projects via the DAG, keeping the best shared proofs.

    Duplicates TARGET into --dest (hardlinked .lake, fresh git history,
    knowledge files carried over), mounts --from read-only, then opens an
    interactive session that:

    \b
      1. agrees with you which shared cones / seeds to merge,
      2. computes the import plan from the source's blueprint DAG
         (`archon dag-carve-plan --project-path <source>`),
      3. for each shared declaration, takes the --prefer side's proof (pulling
         in any source-only auxiliary lemmas it needs); surfaces statement
         conflicts for your call; with --union also imports the source's own
         cones,
      4. adapts README / PROGRESS.md / STRATEGY.md / references.

    \b
    After the session a deterministic gate verifies the result: the DAG
    rebuilds with zero broken \\uses{}, every agreed node is present, and
    (by default) `lake build` succeeds. Only then is the sandbox finalized
    with a fresh outer git history.

    \b
    Examples:
      archon merge ~/proj/B --from ~/proj/A --dest ~/proj/AB
      archon merge ~/proj/B --from ~/proj/A --dest ~/proj/AB --union
      archon merge ~/proj/B --from ~/proj/A --dest ~/proj/AB --prefer target
    """
    if lake not in ("hardlink", "copy", "none"):
        raise typer.BadParameter("--lake must be hardlink | copy | none")
    if prefer not in ("source", "target"):
        raise typer.BadParameter("--prefer must be source | target")
    project_config = load_project_config(Path(target))
    backend = resolve_claude_backend(project_config, cli_value=claude_backend)
    ExtractCommand(
        target, dest,
        merge_source=source, union=union, prefer=prefer,
        lake_mode=lake, resume=resume,
        build_check=build, model=model, backend=backend,
    ).run()
