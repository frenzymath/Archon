"""Typer-decorated `dashboard` entry point."""

from __future__ import annotations

import typer

from .command import DashboardCommand


def dashboard(
    project_path: str = typer.Argument(".", help="Path to Lean project"),
    port: int = typer.Option(
        8080, "--port", "-p",
        help="Starting port. If busy, the next free port is chosen.",
    ),
    dev: bool = typer.Option(False, "--dev", help="Run in dev mode (vite dev + tsx watch)."),
    build_only: bool = typer.Option(False, "--build", help="Build client only, no server."),
    open_browser: bool = typer.Option(False, "--open", help="Open browser after starting."),
    restart: bool = typer.Option(
        False, "--restart",
        help="Stop the previous dashboard for this project on the chosen port "
             "before launching a new one. Without this flag, multiple dashboards "
             "for the same project coexist on different ports.",
    ),
) -> None:
    """Start the web dashboard for real-time monitoring.

    Shows iteration progress, parallel prover status, agent logs with
    live streaming, and proof journal milestones.

    Multiple dashboards for the same project can run simultaneously —
    each is given a different port. Pass ``--restart`` to instead replace
    the previous dashboard on the requested port.
    """
    DashboardCommand(
        project_path,
        port=port,
        dev=dev,
        build_only=build_only,
        open_browser=open_browser,
        restart=restart,
    ).run()
