"""Projects scope — a read-only lens over several Archon projects.

A *scope* is a directory with a top-level ``peers.yaml`` listing member
projects (in the folder or anywhere on the machine). Unlike a project's
``.archon/peers.yaml`` (read-permissions for one project), a scope is a pure
observer/orchestrator: ``archon scope`` builds the union *merge DAG* across
members and a deterministic *roadmap* (who unblocks whom, duplicated work,
what to extract). It is never itself an Archon project.
"""

from .resolve import (
    SCOPE_DIR,
    SCOPE_PEERS_FILE,
    is_scope,
    resolve_members,
    scope_dir,
    scope_peers_path,
    write_template,
)

__all__ = [
    "SCOPE_DIR",
    "SCOPE_PEERS_FILE",
    "is_scope",
    "resolve_members",
    "scope_dir",
    "scope_peers_path",
    "write_template",
]
