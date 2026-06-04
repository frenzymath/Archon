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
    QUERY_VERBS,
    build_graph,
    build_graph_at_commit,
    compute_carve_plan,
    compute_gaps,
    format_carve_plan_text,
    format_json,
    format_markdown,
    format_query_text,
    run_query,
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


def dag_query(
    verb: str = typer.Argument(
        ...,
        help="What to ask the graph: " + " | ".join(QUERY_VERBS),
    ),
    project_path: str = typer.Option(".", "--project-path", help="Path to Lean project"),
    node: str = typer.Option(
        "", "--node",
        help="Target node id (required for `ancestors` / `node` / `cone`; "
             "`cone` accepts a comma-separated seed list)."
    ),
    limit: int = typer.Option(50, "--limit", help="Max nodes to return (0 = all)."),
    sort: str = typer.Option(
        "", "--sort", help="Rank results: effort | deps | impact."
    ),
    complement: bool = typer.Option(
        False, "--complement",
        help="With `cone`: return every node NOT in the seeds' closure."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of text."),
) -> None:
    """Query the leandag blueprint graph (read-only) for a specific node set.

    A focused navigation surface over the same graph the dashboard DAG page
    and the loop use — so agents/subagents can ask "what's the frontier?",
    "what are the ∞ holes?", or "what does X transitively depend on?"
    (`--node X ancestors`) without dumping the whole graph. Examples:

        archon dag-query frontier --sort impact --limit 20
        archon dag-query gaps
        archon dag-query ancestors --node thm:main --json
        archon dag-query cone --node thm:a,thm:b --complement --json
    """
    import json
    res = run_query(
        Path(project_path).resolve(), verb,
        node=node or None, limit=limit, sort=sort or None,
        complement=complement,
    )
    if as_json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(format_query_text(res))
    # Exit non-zero on a genuine error (unknown verb, missing node, leandag
    # unavailable) so callers/scripts can detect failure; the message/JSON is
    # already printed above.
    if res.get("error"):
        raise typer.Exit(1)


def dag_carve_plan(
    project_path: str = typer.Option(".", "--project-path", help="Path to Lean project"),
    node: list[str] = typer.Option(
        ..., "--node",
        help="Seed declaration label (repeatable, or comma-separated)."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON instead of text."),
) -> None:
    """Compute the deterministic carve plan for extracting the seeds' cone.

    The core instrument of `archon extract`: takes seed declarations, computes
    their dependency closure in the blueprint DAG, and rolls the result up per
    .lean file and per blueprint chapter — `keep` (fully in cone), `mixed`
    (needs surgery; lists exactly which blueprint nodes to carve out),
    `imported` (out of cone but kept code imports it — Lean import edges the
    \\uses{} graph cannot see), `drop` (safe to delete). The extract session
    reviews and executes this plan; it does not invent its own.

        archon dag-carve-plan --node thm:picard_representable --json
    """
    import json
    seeds = [s.strip() for arg in node for s in arg.split(",") if s.strip()]
    plan = compute_carve_plan(Path(project_path).resolve(), seeds)
    print(json.dumps(plan, ensure_ascii=False) if as_json
          else format_carve_plan_text(plan))
    if plan.get("error"):
        raise typer.Exit(1)
