"""Archon CLI entrypoint."""

from typing import Optional

import click
import typer
import typer.core

from archon import __version__
from archon import log

from rich.console import Console
from rich.panel import Panel
from rich.text import Text


class _BannerGroup(typer.core.TyperGroup):
    """Typer group that prints the Archon banner before help output."""

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        log.banner(__version__)
        super().format_help(ctx, formatter)

        # ── Examples panel ────────────────────────────────────
        
        console = Console()

        # ── Remark panel ──────────────────────────────────────
        remark = (
            "If you don't know where to start, the typical workflow is:\n\n"
            "  [bold cyan]1.[/bold cyan] archon setup       → install system dependencies\n"
            "  [bold cyan]2.[/bold cyan] cd project/dir     → navigate to your project directory\n"
            "  [bold cyan]3.[/bold cyan] archon init .      → create a project and initialize it with Lean 4\n"
            "  [bold cyan]4.[/bold cyan] archon loop        → run autonomous formalization\n"
            "  [bold cyan]5.[/bold cyan] archon discuss .   → understand blockers and provide hints\n"
            "  [bold cyan]6.[/bold cyan] archon dashboard . → visualize agent activity and project status\n\n"
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


# ── register commands ─────────────────────────────────────────────────

from archon.commands.init import init  # noqa: E402
from archon.commands.loop import loop  # noqa: E402
from archon.commands.doctor import doctor  # noqa: E402
from archon.commands.dashboard import dashboard  # noqa: E402
from archon.commands.setup import setup  # noqa: E402
from archon.commands.prove import prove  # noqa: E402
from archon.commands.update import update  # noqa: E402
from archon.commands.discuss import discuss  # noqa: E402
from archon.commands.version import version as version_cmd  # noqa: E402

app.command()(init)
app.command()(loop)
app.command()(doctor)
app.command()(dashboard)
app.command()(prove)
app.command()(setup)
app.command()(update)
app.command()(discuss)
app.command("version")(version_cmd)

if __name__ == "__main__":
    app()