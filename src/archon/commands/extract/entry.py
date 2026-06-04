"""Typer entry point for ``archon extract``."""

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


def extract(
    parent: str = typer.Argument(
        ".", help="The archon project to extract from (never modified)."
    ),
    dest: str = typer.Option(
        ..., "--dest", "-d",
        help="Where to create the subproject sandbox (must not exist).",
    ),
    merge: Optional[str] = typer.Option(
        None, "--merge",
        help="Merge mode: a second archon project (read-only) whose cones the "
             "session imports into the sandbox. Requires identical "
             "lean-toolchain and mathlib pins.",
    ),
    lake: str = typer.Option(
        "hardlink", "--lake",
        help="How to materialize .lake in the sandbox: 'hardlink' (cp -al, "
             "instant, ~0 disk), 'copy' (full copy), 'none' (skip; run "
             "`lake exe cache get` yourself).",
    ),
    resume: bool = typer.Option(
        False, "--resume",
        help="Re-enter an existing sandbox (skip duplication) — e.g. after a "
             "failed verify gate.",
    ),
    build: bool = typer.Option(
        False, "--build",
        help="Run `lake build` as part of the verify gate (slow but thorough).",
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
    """Extract a subproject from an archon project (or merge two) via the DAG.

    Duplicates PARENT into --dest (hardlinked .lake, fresh git history,
    knowledge files carried over), then opens an interactive session that:

    \b
      1. agrees the scope with you (seed declarations / a topic),
      2. computes the deterministic carve plan from the blueprint DAG
         (`archon dag-carve-plan`) and gets your sign-off,
      3. carves the sandbox down to the seeds' dependency cone — losing no
         dependency (Lean-import riders are kept automatically),
      4. adapts README / PROGRESS.md / STRATEGY.md / references to the new scope.

    \b
    After the session a deterministic gate verifies the result: the DAG
    rebuilds with zero broken \\uses{}, every agreed closure node is present,
    and (with --build) `lake build` succeeds. Only then is the sandbox
    finalized with a fresh outer git history.

    \b
    Examples:
      archon extract ~/proj/Jacobian --dest ~/proj/Picard-Representability
      archon extract ~/proj/A --dest ~/proj/AB --merge ~/proj/B
      archon extract ~/proj/Jacobian --dest ~/proj/Picard --resume --build
    """
    if lake not in ("hardlink", "copy", "none"):
        raise typer.BadParameter("--lake must be hardlink | copy | none")
    project_config = load_project_config(Path(parent))
    backend = resolve_claude_backend(project_config, cli_value=claude_backend)
    ExtractCommand(
        parent, dest,
        merge_source=merge, lake_mode=lake, resume=resume,
        build_check=build, model=model, backend=backend,
    ).run()
