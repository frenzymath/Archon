"""Read ``.archon/peers.yaml`` — the project's read-only awareness scope.

A project may declare *peer* Archon projects whose dependency graph (DAG) it is
allowed to read. This is purely **read-only awareness**: nothing is merged. When
the file is present and non-empty, the loop surfaces peers' progress to the plan
agent (so it can reuse a declaration a peer already proved instead of
duplicating it) and the dashboard can switch between the projects in scope.

The file lives at ``.archon/peers.yaml`` (so it is gitignored along with the
rest of ``.archon/`` — peer paths are machine-local). Schema::

    # Non-empty ⇒ this project may read the matched peers' DAGs.
    read:
      - ~/archon/picard-*        # filesystem globs; ~ and $VARS expanded
      - ~/work/core
    no-access:
      - ~/archon/picard-scratch  # carve-outs; deny wins over read

Semantics:

* Patterns are **filesystem globs** (``*``, ``?``, ``**``, ``[...]``) — the same
  family ``archon-protected.yaml`` uses, deliberately *not* regex: this file is
  hand-edited and globs fail loudly and predictably.
* ``~`` and ``$VARS`` are expanded; relative patterns resolve against the
  project directory.
* A match is kept only if it is an **actual Archon project** (has a ``.archon/``
  directory) and is not the project itself.
* ``no-access`` (deny) wins over ``read`` (allow): allow a broad glob, then
  carve out exceptions.
* Reads are **directed**: A listing B grants A read of B, not the reverse.
"""

from __future__ import annotations

import glob
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import typer
import yaml

PEERS_RELPATH = os.path.join(".archon", "peers.yaml")

_TEMPLATE = """\
# peers.yaml — projects whose DAG this project may read (read-only awareness).
#
# Non-empty ⇒ `archon loop` surfaces these peers' progress to the plan agent
# (so it can reuse what a peer already proved instead of duplicating it), and
# the dashboard can switch between the projects in scope.
#
# Patterns are filesystem globs (*, ?, **, [...]), like archon-protected.yaml —
# not regex. `~` and $VARS are expanded; relative paths resolve against this
# project. `no-access` carve-outs win over `read`. Only real Archon projects
# (those with a `.archon/` dir) are kept. This file lives under .archon/, so it
# is machine-local (gitignored) — peer paths differ per machine.
#
# Run `archon peers` to see exactly what your globs resolve to.

read: []
  # - ~/archon/picard-*
  # - ~/work/core

no-access: []
  # - ~/archon/picard-scratch
"""


@dataclass
class Peer:
    """One resolved peer Archon project."""
    name: str           # display name (unique within the resolved set)
    path: str           # absolute, real path to the project root
    has_dag: bool       # whether .leandag/dag.json exists (a built DAG)


@dataclass
class PeersConfig:
    """Parsed ``.archon/peers.yaml``."""
    path: Path
    read_globs: list[str] = field(default_factory=list)
    deny_globs: list[str] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        """True when the file declares at least one read glob."""
        return bool(self.read_globs)


def peers_file_path(project_path: Path) -> Path:
    return project_path / PEERS_RELPATH


def exists(project_path: Path) -> bool:
    return peers_file_path(project_path).exists()


def write_template(project_path: Path) -> Path:
    """Write the commented empty template (protects nothing). Returns the path."""
    target = peers_file_path(project_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_TEMPLATE, encoding="utf-8")
    return target


def _as_glob_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(x) for x in value if isinstance(x, (str, int, float)) and str(x).strip()]
    return []


def parse_peers_file(path: Path) -> tuple[PeersConfig, list[str]]:
    """Parse a peers.yaml at *path*. Returns (config, schema-problems).

    Location-agnostic: works for a project's ``.archon/peers.yaml`` and for a
    *scope* root's top-level ``peers.yaml`` (same schema, different home).
    """
    empty = PeersConfig(path=path)
    if not path.exists():
        return empty, []

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        return empty, [f"YAML parse error: {e}"]

    if not isinstance(raw, dict):
        return empty, ["top level must be a mapping with `read:` / `no-access:` keys"]

    problems: list[str] = []
    read = _as_glob_list(raw.get("read"))
    # Accept both `no-access` (yaml-friendly) and `no_access`.
    deny = _as_glob_list(raw.get("no-access"))
    deny += _as_glob_list(raw.get("no_access"))

    for k in raw:
        if k not in ("read", "no-access", "no_access"):
            problems.append(f"unrecognized key {k!r} (expected `read` / `no-access`)")

    return PeersConfig(path=path, read_globs=read, deny_globs=deny), problems


def load_with_problems(project_path: Path) -> tuple[PeersConfig, list[str]]:
    """Parse ``.archon/peers.yaml``. Returns (config, schema-problems)."""
    return parse_peers_file(peers_file_path(project_path))


def load(project_path: Path) -> PeersConfig:
    cfg, _ = load_with_problems(project_path)
    return cfg


def is_enabled(project_path: Path) -> bool:
    """True when peers.yaml exists and declares at least one read glob."""
    return load(project_path).is_active


def _expand_dirs(patterns: list[str], base: Path) -> set[str]:
    """Expand globs to a set of real (resolved) directory paths."""
    out: set[str] = set()
    for pat in patterns:
        p = os.path.expandvars(os.path.expanduser(pat))
        if not os.path.isabs(p):
            p = os.path.join(str(base), p)
        for match in glob.glob(p, recursive=True):
            if os.path.isdir(match):
                out.add(os.path.realpath(match))
    return out


def _is_archon_project(path: str) -> bool:
    return os.path.isdir(os.path.join(path, ".archon"))


def _has_dag(path: str) -> bool:
    return os.path.isfile(os.path.join(path, ".leandag", "dag.json"))


def _assign_names(paths: list[str]) -> dict[str, str]:
    """Map each path to a unique display name (basename, disambiguated on clash)."""
    names: dict[str, str] = {}
    base_counts: dict[str, int] = {}
    for p in paths:
        base_counts[os.path.basename(p)] = base_counts.get(os.path.basename(p), 0) + 1
    for p in paths:
        base = os.path.basename(p)
        if base_counts[base] > 1:
            parent = os.path.basename(os.path.dirname(p))
            names[p] = f"{parent}/{base}"
        else:
            names[p] = base
    return names


def resolve_config(cfg: PeersConfig, base: Path) -> list[Peer]:
    """Resolve a parsed peers config against *base* (the directory its globs
    are relative to). Excludes *base* itself and non-Archon dirs; deny wins;
    results are deduplicated and sorted by path.

    Shared by per-project resolution (``base`` = the project) and *scope*
    resolution (``base`` = the scope root holding the top-level peers.yaml).
    """
    if not cfg.read_globs:
        return []

    self_real = os.path.realpath(str(base))
    allowed = _expand_dirs(cfg.read_globs, base)
    denied = _expand_dirs(cfg.deny_globs, base)

    kept = sorted(
        p for p in (allowed - denied)
        if p != self_real and _is_archon_project(p)
    )
    names = _assign_names(kept)
    return [Peer(name=names[p], path=p, has_dag=_has_dag(p)) for p in kept]


def resolve_peers(project_path: Path) -> list[Peer]:
    """Resolve peers.yaml globs to the set of allowed peer Archon projects.

    Deny wins over allow; non-Archon dirs and the project itself are dropped;
    results are deduplicated and sorted by path.
    """
    return resolve_config(load(project_path), project_path)


# ── Reading a peer's DAG (read-only, never rebuilds) ────────────────────────

def read_peer_dag(peer_path: str) -> dict | None:
    """Load a peer's cached ``.leandag/dag.json`` (read-only). None if absent."""
    dag_file = os.path.join(peer_path, ".leandag", "dag.json")
    try:
        with open(dag_file, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _split_lean_names(lean_name: object) -> list[str]:
    """A node's ``lean_name`` may be comma-separated for multi-decl nodes."""
    if not isinstance(lean_name, str):
        return []
    return [n.strip() for n in lean_name.split(",") if n.strip()]


def available_lean_names(dag: dict) -> set[str]:
    """Lean names a peer offers for reuse: proved-locally or mathlib-backed."""
    out: set[str] = set()
    for node in dag.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        if node.get("proved") or node.get("mathlib_ok"):
            out.update(_split_lean_names(node.get("lean_name")))
    return out


def needed_lean_names(dag: dict) -> set[str]:
    """Lean names a project still needs: nodes not yet proved/mathlib-backed."""
    out: set[str] = set()
    for node in dag.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        if node.get("proved") or node.get("mathlib_ok"):
            continue
        out.update(_split_lean_names(node.get("lean_name")))
    return out


def declared_lean_names(dag: dict) -> set[str]:
    """Every Lean name a project declares (proved or not), for the merge DAG.

    Equivalent to ``available_lean_names(dag) | needed_lean_names(dag)`` but
    computed in one pass. Nodes without a ``lean_name`` (e.g. exercises) are
    skipped.
    """
    out: set[str] = set()
    for node in dag.get("nodes", []) or []:
        if isinstance(node, dict):
            out.update(_split_lean_names(node.get("lean_name")))
    return out


# ── CLI ─────────────────────────────────────────────────────────────────────

peers_app = typer.Typer(
    name="peers",
    help="Peer projects this one may read, and the inbox to give them feedback.",
    invoke_without_command=True,
    no_args_is_help=False,
)


@peers_app.callback(invoke_without_command=True)
def peers_cli(
    ctx: typer.Context,
    project_path: str = typer.Option(".", "--project-path", help="Path to the Archon project"),
    as_json: bool = typer.Option(False, "--json", help="Emit the resolved peer list as JSON"),
) -> None:
    """Show the peer projects this project may read (resolves ``.archon/peers.yaml``).

    Validates the file, expands the read/no-access globs, and lists the Archon
    projects they resolve to — analogous to ``archon protect-check``. The
    dashboard consumes ``--json`` to build its project switcher. Run
    ``archon peers pr`` / ``archon peers issue`` to leave a peer upstream
    feedback on one of its declarations.
    """
    if ctx.invoked_subcommand is not None:
        return

    root = Path(project_path).resolve()
    cfg, problems = load_with_problems(root)
    peers = resolve_peers(root)

    if as_json:
        print(json.dumps({
            "active": cfg.is_active,
            "problems": problems,
            "peers": [asdict(p) for p in peers],
        }, indent=2))
        if problems:
            raise typer.Exit(1)
        return

    if not cfg.path.exists():
        print(f"No {PEERS_RELPATH} at {root} — no peer projects in scope.")
        return
    for p in problems:
        print(f"- [schema] {p}")
    if not cfg.is_active:
        print("peers.yaml has no `read:` globs — no peer projects in scope.")
        return
    if not peers:
        print("`read:` globs matched no Archon projects "
              "(matches must contain a `.archon/` dir, and `no-access` wins).")
    for p in peers:
        dag = "dag" if p.has_dag else "no-dag-yet"
        print(f"- [{dag}] {p.name}  →  {p.path}")
    if problems:
        raise typer.Exit(1)


def _match_target(peers: list[Peer], target: str) -> Peer | None:
    """Resolve a ``--target`` (a peer name or a path) to a readable peer."""
    for p in peers:
        if p.name == target:
            return p
    real = os.path.realpath(os.path.expanduser(target))
    for p in peers:
        if os.path.realpath(p.path) == real:
            return p
    return None


@peers_app.command("pr")
def peers_pr(
    target: str = typer.Option(..., "--target", "-t", help="Peer to address (its name from `archon peers`, or a path)"),
    lean_name: str = typer.Option(..., "--lean-name", "-n", help="The peer declaration this PR generalizes"),
    code: str = typer.Option(..., "--code", "-c", help="The generalized code from your codebase"),
    rationale: str = typer.Option("", "--rationale", "-r", help="Why this generalization is needed"),
    project_path: str = typer.Option(".", "--project-path", help="The authoring Archon project"),
) -> None:
    """Submit a code generalization (Pull Request) to a peer."""
    _write_feedback(target, project_path, "pr", code, lean_name, rationale)

@peers_app.command("issue")
def peers_issue(
    target: str = typer.Option(..., "--target", "-t", help="Peer to address (its name from `archon peers`, or a path)"),
    title: str = typer.Option(..., "--title", help="Issue title"),
    body: str = typer.Option(..., "--body", "-b", help="Issue description"),
    lean_name: str = typer.Option("", "--lean-name", "-n", help="Optional: the peer declaration this issue is about"),
    project_path: str = typer.Option(".", "--project-path", help="The authoring Archon project"),
) -> None:
    """Submit a mathematical or architectural issue to a peer."""
    _write_feedback(target, project_path, "issue", f"{title}\n\n{body}", lean_name, rationale="")

def _write_feedback(target: str, project_path: str, note_type: str, payload: str, lean_name: str, rationale: str):
    from archon.commands.tooling import inbox, project_config

    root = Path(project_path).resolve()
    author = project_config.project_name(root)
    peers = resolve_peers(root)
    if not peers:
        print("No peer projects in scope — nothing to address. "
              "Declare peers in `.archon/peers.yaml` first.")
        raise typer.Exit(1)

    peer = _match_target(peers, target)
    if peer is None:
        names = ", ".join(p.name for p in peers) or "(none)"
        print(f"'{target}' is not a peer you may read. In scope: {names}.")
        raise typer.Exit(1)

    written = inbox.write_note(
        Path(peer.path), author, note_type, payload, lean_name, rationale,
    )
    print(f"Submitted {note_type.upper()} to '{peer.name}' (from '{author}'): {written}")


@peers_app.command("inbox")
def peers_inbox(
    project_path: str = typer.Option(".", "--project-path", help="The project whose inbox to read"),
    show_all: bool = typer.Option(False, "--all", help="Include resolved (accepted/declined) notes, not just open ones"),
    as_json: bool = typer.Option(False, "--json", help="Emit the inbox as JSON"),
) -> None:
    """Show PRs / issues peers left for THIS project.

    The receiving side of ``archon peers pr`` / ``archon peers issue``. Each note
    prints its short id; resolve one you have acted on (or won't) with
    ``archon peers accept --id <id>`` / ``archon peers decline --id <id>``.
    """
    from archon.commands.tooling import inbox

    root = Path(project_path).resolve()
    boxes = inbox.load_all(root)

    if as_json:
        print(json.dumps([
            {
                "author": b.author,
                "notes": [
                    {"id": n.id, "type": n.type, "lean_name": n.lean_name,
                     "payload": n.payload, "rationale": n.rationale,
                     "status": n.status}
                    for n in b.notes
                    if show_all or n.status == "open"
                ],
            }
            for b in boxes
        ], indent=2))
        return

    shown = 0
    for b in boxes:
        notes = [n for n in b.notes if show_all or n.status == "open"]
        if not notes:
            continue
        print(f"\nfrom {b.author}:")
        for n in notes:
            badge = "" if n.status == "open" else f" [{n.status}]"
            scope = f"`{n.lean_name}` " if n.lean_name else ""
            print(f"  - [{n.type}] {scope}({n.id}){badge}: {n.payload}")
            if n.rationale:
                print(f"      why: {n.rationale}")
        shown += len(notes)
    if shown == 0:
        scope = "notes" if show_all else "open notes"
        print(f"No {scope} in this project's inbox.")


def _set_inbox_status(project_path: str, note_id: str, status: str) -> None:
    from archon.commands.tooling import inbox

    root = Path(project_path).resolve()
    hit = inbox.find_note_by_id(root, note_id)
    if hit is None:
        print(f"No note with id '{note_id}' in this project's inbox. "
              "Run `archon peers inbox` to see ids.")
        raise typer.Exit(1)
    ib, note = hit
    inbox.set_status_by_id(root, note_id, status)
    label = f"`{note.lean_name}`" if note.lean_name else f"{note.type}"
    print(f"Marked {label} ({note_id}) from '{ib.author}' as {status}.")


@peers_app.command("accept")
def peers_accept(
    note_id: str = typer.Option(..., "--id", "-i", help="The note id (from `archon peers inbox`)"),
    project_path: str = typer.Option(".", "--project-path", help="The project whose inbox to update"),
) -> None:
    """Mark a peer's note accepted (you acted on it). Stops surfacing it."""
    _set_inbox_status(project_path, note_id, "accepted")


@peers_app.command("decline")
def peers_decline(
    note_id: str = typer.Option(..., "--id", "-i", help="The note id (from `archon peers inbox`)"),
    project_path: str = typer.Option(".", "--project-path", help="The project whose inbox to update"),
) -> None:
    """Mark a peer's note declined (you won't act on it). Stops surfacing it."""
    _set_inbox_status(project_path, note_id, "declined")
