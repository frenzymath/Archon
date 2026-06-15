"""``archon scope dashboard`` — the dashboard over a whole scope.

The dashboard server already renders a project switcher and a union ("Meta")
DAG across a base project plus its peers. Scope mode reuses that UI: it launches
the normal dashboard bound to a *host* member, but injects the scope's full
membership as the peer set (via ``ARCHON_SCOPE_PEERS``, which the server's
``loadPeers`` prefers). The host shows as the current project; the others are
the switchable peers, and the union view spans the whole scope.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import typer

from archon import log
from archon.commands.dashboard.command import DashboardCommand

from . import resolve as scope_mod

SCOPE_PEERS_ENV = "ARCHON_SCOPE_PEERS"


def _pick_host(members: list, requested: str | None):
    """Choose which member the dashboard binds to (the 'current' project)."""
    if requested:
        for m in members:
            if m.name == requested or os.path.realpath(m.path) == os.path.realpath(
                os.path.expanduser(requested)
            ):
                return m
        names = ", ".join(m.name for m in members)
        log.error(f"'{requested}' is not a member. In scope: {names}.")
        raise typer.Exit(1)
    # Default: first member with a built DAG (so the graph view has content),
    # else the first member.
    for m in members:
        if m.has_dag:
            return m
    return members[0]


def scope_dashboard(
    path: str = typer.Option(".", "--path", help="The scope directory"),
    member: Optional[str] = typer.Option(
        None, "--member", "-m",
        help="Which member to bind as the host/current project (default: first with a DAG).",
    ),
    port: int = typer.Option(8080, "--port", "-p", help="Starting port (next free is used if busy)."),
    open_browser: bool = typer.Option(False, "--open", help="Open the browser after starting."),
    restart: bool = typer.Option(False, "--restart", help="Replace a previous dashboard on this port."),
) -> None:
    """Launch the dashboard over the scope (project switcher + union DAG across members)."""
    root = Path(path).resolve()
    if not scope_mod.is_scope(root):
        log.error(f"No {scope_mod.SCOPE_PEERS_FILE} at {root} — not a scope. "
                  f"Run `archon scope init` here first.")
        raise typer.Exit(1)

    members = scope_mod.resolve_members(root)
    if not members:
        log.error("No member Archon projects in scope — edit peers.yaml first.")
        raise typer.Exit(1)

    host = _pick_host(members, member)
    peers_payload = [
        {"name": m.name, "path": m.path, "has_dag": m.has_dag}
        for m in members if os.path.realpath(m.path) != os.path.realpath(host.path)
    ]
    # The server inherits this env (ServerProcess.spawn copies os.environ);
    # loadPeers prefers it over the host's own peers.yaml.
    os.environ[SCOPE_PEERS_ENV] = json.dumps(peers_payload)

    log.key_value({
        "Scope": str(root),
        "Host project": f"{host.name}  ({host.path})",
        "Peers in switcher": ", ".join(p["name"] for p in peers_payload) or "(none)",
    })

    DashboardCommand(
        host.path,
        port=port,
        open_browser=open_browser,
        restart=restart,
    ).run()
