"""Archon CLI entrypoint."""

from typing import Optional

import click
import typer
import typer.core

from archon import __version__
from archon import log

from rich.console import Console
from rich.panel import Panel


class _BannerGroup(typer.core.TyperGroup):
    """Typer group that prints the Archon banner before help output."""

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        log.banner(__version__)
        super().format_help(ctx, formatter)

        # ── Remark panel ──────────────────────────────────────
        
        console = Console()

        remark = (
            "If you don't know where to start, the typical workflow is:\n\n"
            "  [bold cyan]1.[/bold cyan] archon setup       → install system dependencies\n"
            "  [bold cyan]2.[/bold cyan] cd project/dir     → navigate to your project directory\n"
            "  [bold cyan]3.[/bold cyan] archon init .      → create a project and initialize it with Lean 4\n"
            "  [bold cyan]4.[/bold cyan] archon dag         → elaborate the full blueprint (optional, before loop)\n"
            "  [bold cyan]5.[/bold cyan] archon loop        → run autonomous formalization\n"
            "  [bold cyan]6.[/bold cyan] archon discuss .   → understand blockers and provide hints\n"
            "  [bold cyan]7.[/bold cyan] archon dashboard . → visualize agent activity and project status\n\n"
            "[dim]Run [bold]archon <command> -h[/bold] for details on any command.[/dim]"
        )
        console.print(Panel(
            remark,
            title="Remark",
            title_align="left",
            border_style="dim",
            padding=(1, 2),
        ))

app = typer.Typer(
    cls=_BannerGroup,
    help="Autonomous Lean 4 Formalization",
    invoke_without_command=True,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    rich_markup_mode="rich"
)


def _version_callback(value: bool) -> None:
    if value:
        log.banner(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-V",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Autonomous Lean 4 Formalization."""
    log.banner(__version__)
    # Load .archon/.env from the current working directory if it exists.
    # Cheap (idempotent, no-op when file missing) so doing it on every
    # command keeps "I just edited .env" working without an extra step.
    from pathlib import Path as _Path
    from archon.commands.tooling.env_loader import load_env_file as _load_env
    _load_env(_Path.cwd())


# ── register commands ─────────────────────────────────────────────────

from archon.commands.init import init  # noqa: E402
from archon.commands.loop import loop  # noqa: E402
from archon.commands.doctor import doctor  # noqa: E402
from archon.commands.dashboard import dashboard  # noqa: E402
from archon.commands.setup import setup  # noqa: E402
from archon.commands.prove import prove  # noqa: E402
from archon.commands.update import update  # noqa: E402
from archon.commands.discuss import discuss  # noqa: E402
from archon.commands.refactor import app as refactor_app  # noqa: E402
from archon.commands.branch import branch, inner_log  # noqa: E402
from archon.commands.version import version as version_cmd  # noqa: E402
from archon.commands.subagent import subagent_command  # noqa: E402
from archon.commands.migrate import app as migrate_app  # noqa: E402
from archon.commands.dag import dag  # noqa: E402
from archon.commands.extract import extract, merge  # noqa: E402
from archon.commands.dag.gaps_entry import dag_carve_plan, dag_gaps, dag_graph, dag_query  # noqa: E402

app.command()(init)
app.command()(dag)
# Agent/internal plumbing — invoked by prompts and the loop, not by humans.
# Hidden from `archon --help` (still fully runnable + `<cmd> --help` works)
# so the top-level surface stays focused on the user workflow.
app.command("dag-gaps", hidden=True)(dag_gaps)
app.command("dag-graph", hidden=True)(dag_graph)
app.command("dag-query", hidden=True)(dag_query)
app.command("dag-carve-plan", hidden=True)(dag_carve_plan)
from archon.commands.loop.blueprint_doctor import blueprint_doctor_cli  # noqa: E402
app.command("blueprint-doctor")(blueprint_doctor_cli)
from archon.commands.tooling.protect import protect_check_cli  # noqa: E402
app.command("protect-check", hidden=True)(protect_check_cli)
from archon.commands.tooling.peers import peers_app  # noqa: E402
app.add_typer(peers_app, name="peers")
from archon.commands.scope.command import app as scope_app  # noqa: E402
app.add_typer(scope_app, name="scope")
app.command()(extract)
app.command()(merge)
app.command()(loop)
app.command()(doctor)
app.command()(dashboard)
app.command()(prove)
app.command()(setup)
app.command()(update)
app.command()(discuss)
app.command("branch")(branch)
app.command("log")(inner_log)
app.command("version")(version_cmd)
app.add_typer(refactor_app, name="refactor")
app.command("subagent", hidden=True)(subagent_command)
app.add_typer(migrate_app, name="migrate")

if __name__ == "__main__":
    app()