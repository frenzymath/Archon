"""``archon scope`` — operate on a directory of Archon projects.

A scope is a folder with a top-level ``peers.yaml`` listing member projects.
These commands resolve that membership, build the union merge DAG, and emit a
deterministic roadmap. The agentic ``discuss`` and the ``dashboard`` views are
the next slice; this is the read-only analysis core.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from archon import log

from . import resolve as scope_mod
from .dashboard import scope_dashboard
from .discuss import scope_discuss
from .merge_dag import build_merge_dag
from .roadmap import build_roadmap, render_markdown

app = typer.Typer(
    name="scope",
    help="Operate on a directory of Archon projects (membership, merge DAG, roadmap).",
    no_args_is_help=True,
)


def _require_scope(root: Path) -> None:
    if not scope_mod.is_scope(root):
        log.error(
            f"No {scope_mod.SCOPE_PEERS_FILE} at {root} — not a scope. "
            f"Run `archon scope init` here first."
        )
        raise typer.Exit(1)


@app.command("init")
def scope_init(
    path: str = typer.Argument(".", help="Directory to turn into a scope"),
) -> None:
    """Scaffold a scope: a top-level peers.yaml + .archon-scope/ directory."""
    root = Path(path).resolve()
    existed = scope_mod.is_scope(root)
    manifest = scope_mod.write_template(root)
    if existed:
        log.step(f"Scope already initialized — preserved {manifest}")
    else:
        log.success(f"Initialized scope at {root}")
    log.step(f"Edit {manifest} to list member projects, then `archon scope ls`.")


@app.command("add")
def scope_add(
    project: str = typer.Argument(..., help="A member project path/glob to append to read:"),
    path: str = typer.Option(".", "--path", help="The scope directory"),
) -> None:
    """Append a member path/glob to the scope's peers.yaml read list.

    A thin convenience over hand-editing the manifest. Idempotent: an already
    listed pattern is not duplicated.
    """
    root = Path(path).resolve()
    _require_scope(root)
    cfg, _ = scope_mod.load_with_problems(root)
    if project in cfg.read_globs:
        log.step(f"'{project}' is already a member pattern — no change.")
        return
    manifest = scope_mod.scope_peers_path(root)
    # Re-emit a minimal valid manifest preserving existing read/deny globs.
    import yaml
    data = {
        "read": cfg.read_globs + [project],
        "no-access": cfg.deny_globs,
    }
    manifest.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                        encoding="utf-8")
    log.success(f"Added '{project}' to {manifest}")


@app.command("ls")
def scope_ls(
    path: str = typer.Option(".", "--path", help="The scope directory"),
    as_json: bool = typer.Option(False, "--json", help="Emit resolved members as JSON"),
) -> None:
    """List the member projects this scope resolves to."""
    root = Path(path).resolve()
    cfg, problems = scope_mod.load_with_problems(root)
    members = scope_mod.resolve_members(root)

    if as_json:
        print(json.dumps({
            "active": cfg.is_active,
            "problems": problems,
            "members": [
                {"name": m.name, "path": m.path, "has_dag": m.has_dag}
                for m in members
            ],
        }, indent=2))
        raise typer.Exit(1 if problems else 0)

    _require_scope(root)
    for p in problems:
        print(f"- [schema] {p}")
    if not members:
        print("No member Archon projects resolved "
              "(matches need a `.archon/` dir; `no-access` wins).")
        raise typer.Exit(1 if problems else 0)
    for m in members:
        tag = "dag" if m.has_dag else "no-dag-yet"
        print(f"- [{tag}] {m.name}  →  {m.path}")
    if problems:
        raise typer.Exit(1)


@app.command("graph")
def scope_graph(
    path: str = typer.Option(".", "--path", help="The scope directory"),
    as_json: bool = typer.Option(False, "--json", help="Print the merge DAG as JSON instead of writing it"),
) -> None:
    """Build the union merge DAG across members; cache it under .archon-scope/."""
    root = Path(path).resolve()
    _require_scope(root)
    members = scope_mod.resolve_members(root)
    mdag = build_merge_dag(members)

    if as_json:
        print(json.dumps(mdag.to_dict(), indent=2))
        return

    out = scope_mod.scope_dir(root) / "merge-dag.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(mdag.to_dict(), indent=2), encoding="utf-8")
    shared = sum(1 for n in mdag.nodes if n.shared)
    log.success(f"Merge DAG: {len(mdag.nodes)} declaration(s) across "
                f"{len(members)} member(s), {shared} shared. → {out}")


@app.command("roadmap")
def scope_roadmap(
    path: str = typer.Option(".", "--path", help="The scope directory"),
    as_json: bool = typer.Option(False, "--json", help="Print the roadmap as JSON instead of writing it"),
) -> None:
    """Analyze the scope: unblock matrix, duplication, leverage, stalled members."""
    root = Path(path).resolve()
    _require_scope(root)
    members = scope_mod.resolve_members(root)
    rm, mdag = build_roadmap(members)

    if as_json:
        print(json.dumps(rm.to_dict(), indent=2))
        return

    sdir = scope_mod.scope_dir(root)
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "merge-dag.json").write_text(
        json.dumps(mdag.to_dict(), indent=2), encoding="utf-8")
    md = render_markdown(rm)
    (sdir / "roadmap.md").write_text(md, encoding="utf-8")
    (sdir / "roadmap.json").write_text(
        json.dumps(rm.to_dict(), indent=2), encoding="utf-8")

    print(md)
    log.success(f"Roadmap written to {sdir / 'roadmap.md'}")


# Interactive + dashboard subcommands (defined in sibling modules).
app.command("discuss")(scope_discuss)
app.command("dashboard")(scope_dashboard)
