"""Resolve a scope root to its member Archon projects.

The scope's membership reuses the exact ``peers.yaml`` machinery — same glob
schema, same deny-wins semantics — but the file lives at the scope **root**
(``<scope>/peers.yaml``) rather than under ``.archon/``. Location is what
distinguishes a scope from a project, so a scope is never mistaken for one.
The scope's own artifacts (cached merge DAG, roadmap output) live in
``<scope>/.archon-scope/``.
"""

from __future__ import annotations

from pathlib import Path

from archon.commands.tooling import peers as peers_mod

SCOPE_PEERS_FILE = "peers.yaml"   # top-level, distinguishing it from a project
SCOPE_DIR = ".archon-scope"        # cached merge DAG, roadmap output, logs


def scope_peers_path(root: Path) -> Path:
    return root / SCOPE_PEERS_FILE


def scope_dir(root: Path) -> Path:
    return root / SCOPE_DIR


def is_scope(root: Path) -> bool:
    """True when *root* has a top-level ``peers.yaml`` (a scope manifest)."""
    return scope_peers_path(root).is_file()


def resolve_members(root: Path) -> list[peers_mod.Peer]:
    """The Archon projects this scope's ``peers.yaml`` resolves to.

    Empty when the manifest is absent or declares no ``read:`` globs. The scope
    root itself and non-Archon directories are excluded (deny wins), matching
    ``archon peers``.
    """
    cfg, _ = peers_mod.parse_peers_file(scope_peers_path(root))
    return peers_mod.resolve_config(cfg, root)


def load_with_problems(root: Path) -> tuple[peers_mod.PeersConfig, list[str]]:
    """Parse the scope manifest, returning (config, schema-problems)."""
    return peers_mod.parse_peers_file(scope_peers_path(root))


_TEMPLATE = """\
# peers.yaml — the members of this Archon *scope* (a read-only lens over
# several projects). `archon scope roadmap` builds the union merge DAG across
# these members and reports who unblocks whom, duplicated work, and what to
# extract. Same glob schema as a project's .archon/peers.yaml: patterns are
# filesystem globs (*, ?, **, [...]); `~`/$VARS expand; relative paths resolve
# against this folder; `no-access` carve-outs win; only real Archon projects
# (those with a `.archon/` dir) are kept. Members may live inside this folder
# or anywhere on the machine. Run `archon scope ls` to see what resolves.

read: []
  # - ./*                 # every Archon project directly inside this folder
  # - ~/work/core
  # - ~/archon/picard-*

no-access: []
  # - ./scratch
"""


def write_template(root: Path) -> Path:
    """Scaffold ``<root>/peers.yaml`` (if absent) and ``<root>/.archon-scope/``.

    Returns the manifest path. An existing manifest is preserved.
    """
    root.mkdir(parents=True, exist_ok=True)
    scope_dir(root).mkdir(parents=True, exist_ok=True)
    manifest = scope_peers_path(root)
    if not manifest.exists():
        manifest.write_text(_TEMPLATE, encoding="utf-8")
    return manifest
