"""Typer entry point for ``archon extract``."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from archon import log
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
        help="DEPRECATED — use `archon merge` instead. Kept as an alias: a "
             "second archon project (read-only) whose cones are imported into "
             "the sandbox.",
        hidden=True,
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
    harness: Optional[str] = typer.Option(
        None, "--harness",
        help="Override the interactive session harness, e.g. codex-gpt.",
    ),
) -> None:
    """Extract a subproject (a dependency cone) from an archon project.

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
    To COMBINE two projects, use `archon merge` instead.

    \b
    Examples:
      archon extract ~/proj/Jacobian --dest ~/proj/Picard-Representability
      archon extract ~/proj/Jacobian --dest ~/proj/Picard --resume --build
    """
    if lake not in ("hardlink", "copy", "none"):
        raise typer.BadParameter("--lake must be hardlink | copy | none")
    if merge is not None:
        log.warn("`archon extract --merge` is deprecated — use "
                 "`archon merge <target> --from <source> --dest <dir>`. "
                 "Running in merge mode for now.")
    project_config = load_project_config(Path(parent))
    backend = resolve_claude_backend(project_config, cli_value=claude_backend)
    ExtractCommand(
        parent, dest,
        merge_source=merge, lake_mode=lake, resume=resume,
        build_check=build, model=model, backend=backend, harness=harness,
    ).run()
