"""Typer entry point for ``archon dag-gaps``.

Prints the blueprint-coverage gap report (uncovered Lean decls, broken
``\\uses{}`` refs, unproved/ready declarations) computed by leandag.
Backs the ``.claude/tools/archon-leandag.py`` wrapper that the dag agent
and blueprint-writers call mid-session.
"""

from __future__ import annotations

from pathlib import Path

import typer

from .leandag_gaps import (
    build_graph,
    build_graph_at_commit,
    compute_gaps,
    format_json,
    format_markdown,
)


def dag_gaps(
    project_path: str = typer.Option(".", "--project-path", help="Path to Lean project"),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of markdown."),
) -> None:
    """Report blueprint-coverage gaps for a Lean project (via leandag)."""
    report = compute_gaps(Path(project_path).resolve())
    print(format_json(report) if as_json else format_markdown(report))


def dag_graph(
    project_path: str = typer.Option(".", "--project-path", help="Path to Lean project"),
    no_cache: bool = typer.Option(
        False, "--no-cache", help="Do not write .leandag/dag.json."
    ),
    commit: str = typer.Option(
        "", "--commit",
        help="Build the DAG as it was at this inner-git commit (in-memory, "
             "never cached). Empty = current working tree.",
    ),
) -> None:
    """Emit the full blueprint DAG as JSON (nodes/edges/meta) on stdout.

    Backs the dashboard's DAG page. With --commit, builds the historical DAG
    from the inner-git tree at that commit without touching .leandag/ (so a
    running loop's live graph is never clobbered). Otherwise computes fresh
    via leandag and (unless --no-cache) writes .leandag/dag.json. Only the
    JSON is printed to stdout so the dashboard can parse it directly.
    """
    import json
    root = Path(project_path).resolve()
    if commit:
        data = build_graph_at_commit(root, commit)
    else:
        data = build_graph(root, write_cache=not no_cache)
    print(json.dumps(data, ensure_ascii=False))
