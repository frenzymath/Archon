"""Show Archon CLI and project version."""

from __future__ import annotations

from pathlib import Path

import typer

from archon import __version__, log
from archon.commands.tooling.version import ProjectVersion


def version(
    project_path: str = typer.Argument(
        ".", help="Path to an Archon project (optional).",
    ),
) -> None:
    """Show the Archon CLI version and, if in a project, the project version.

    [bold]Examples:[/bold]
      [cyan]archon version[/cyan]
      [cyan]archon version /path/to/project[/cyan]
    """
    resolved = Path(project_path).resolve()

    info: dict[str, str] = {"CLI version": __version__}

    state_dir = resolved / ".archon"
    if state_dir.is_dir():
        status = ProjectVersion(resolved).status()
        info["Project"] = str(resolved)
        info["Project version"] = status.project_version or "(missing)"
        info["Status"] = status.state

        log.key_value(info)

        if status.should_warn():
            log.warn(status.message())
        return

    info["Project"] = f"(not an Archon project: {resolved})"
    log.key_value(info)
    