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


def load_with_problems(project_path: Path) -> tuple[PeersConfig, list[str]]:
    """Parse ``.archon/peers.yaml``. Returns (config, schema-problems)."""
    target = peers_file_path(project_path)
    empty = PeersConfig(path=target)
    if not target.exists():
        return empty, []

    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
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

    return PeersConfig(path=target, read_globs=read, deny_globs=deny), problems


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


def resolve_peers(project_path: Path) -> list[Peer]:
    """Resolve peers.yaml globs to the set of allowed peer Archon projects.

    Deny wins over allow; non-Archon dirs and the project itself are dropped;
    results are deduplicated and sorted by path.
    """
    cfg = load(project_path)
    if not cfg.read_globs:
        return []

    self_real = os.path.realpath(str(project_path))
    allowed = _expand_dirs(cfg.read_globs, project_path)
    denied = _expand_dirs(cfg.deny_globs, project_path)

    kept = sorted(
        p for p in (allowed - denied)
        if p != self_real and _is_archon_project(p)
    )
    names = _assign_names(kept)
    return [Peer(name=names[p], path=p, has_dag=_has_dag(p)) for p in kept]


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


# ── CLI ─────────────────────────────────────────────────────────────────────

def peers_cli(
    project_path: str = typer.Option(".", "--project-path", help="Path to the Archon project"),
    as_json: bool = typer.Option(False, "--json", help="Emit the resolved peer list as JSON"),
) -> None:
    """Show the peer projects this project may read (resolves ``.archon/peers.yaml``).

    Validates the file, expands the read/no-access globs, and lists the Archon
    projects they resolve to — analogous to ``archon protect-check``. The
    dashboard consumes ``--json`` to build its project switcher.
    """
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
