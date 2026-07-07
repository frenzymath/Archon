"""Structured console output for Archon CLI.

Wraps Rich to provide a consistent, visually polished CLI experience.
All output is routed through a single Console instance.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

_console = Console()
# Decorative banner goes to stderr so a command's stdout stays clean for
# machine consumers (e.g. `archon dag-gaps --json`). Nothing parses the banner.
_err_console = Console(stderr=True)
_PREFIX = "[bold cyan]\\[ARCHON][/bold cyan]"
_REPO_URL = "https://github.com/frenzymath/Archon"

_BANNER = r"""[bold cyan]   _            _                 
  /_\  _ __ ___| |__   ___  _ __  
 //_\\| '__/ __| '_ \ / _ \| '_ \ 
/  _  \ | | (__| | | | (_) | | | |
\_/ \_/_|  \___|_| |_|\___/|_| |_|[/bold cyan]"""


# ── core output ───────────────────────────────────────────────────────


def banner(version: str) -> None:
    _err_console.print(f"\n{_BANNER}\n")

    meta_text = Text.assemble(
        (f" v{version} ", "bold cyan"),
        "  ",
        (f" {_REPO_URL} ", "dim italic")
    )

    _err_console.print(Rule(title=meta_text, style="cyan", align="left"))
    _err_console.print()


def info(msg: str) -> None:
    _console.print(f"{_PREFIX} {msg}")


def success(msg: str) -> None:
    _console.print(f"{_PREFIX} [green]✓ {msg}[/green]")


def warn(msg: str) -> None:
    _console.print(f"{_PREFIX} [yellow]⚠ {msg}[/yellow]")


def error(msg: str) -> None:
    _console.print(f"{_PREFIX} [bold red]✗ {msg}[/bold red]")


def step(msg: str) -> None:
    """A neutral progress step."""
    _console.print(f"{_PREFIX} [dim]›[/dim] {msg}")


def stub(name: str) -> None:
    """Mark a command as not yet implemented."""
    warn(f"The functionality for '{name}' is not implemented yet.")


# ── structural elements ───────────────────────────────────────────────


def rule(title: str = "", style: str = "cyan") -> None:
    """Print a horizontal rule with an optional centered title."""
    _console.print(Rule(title=title, style=style))


def header(title: str) -> None:
    """Print a prominent section header."""
    _console.print()
    _console.print(Rule(title=f"[bold]{title}[/bold]", style="cyan", align="left"))


def phase(number: int, title: str) -> None:
    """Print a phase header (e.g. 'Phase 1: Plan agent')."""
    _console.print()
    _console.print(
        Rule(title=f"[bold cyan]Phase {number}[/bold cyan]  {title}", style="dim", align="left")
    )


def iteration(current: int, total: int, stage: str, project: str) -> None:
    """Print an iteration banner."""
    _console.print()
    _console.print(
        Panel(
            f"[bold]Stage:[/bold] {stage}  ·  [bold]Project:[/bold] {project}",
            title=f"[bold cyan]Iteration {current}/{total}[/bold cyan]",
            border_style="cyan",
            padding=(0, 2),
        )
    )


# ── tables ────────────────────────────────────────────────────────────


def key_value(data: dict[str, str], title: str = "") -> None:
    """Print a two-column key-value table."""
    table = Table(
        show_header=False,
        border_style="dim",
        padding=(0, 2),
        title=title or None,
        title_style="bold",
    )
    table.add_column("Key", style="bold", no_wrap=True)
    table.add_column("Value")
    for k, v in data.items():
        table.add_row(k, v)
    _console.print(table)


def results_table(
    rows: list[tuple[str, str, str]],
    title: str = "",
) -> None:
    """Print a three-column results table (Name, Status, Detail).

    Status values are auto-colored: done/success → green, error → red,
    running → yellow, others → dim.
    """
    table = Table(
        border_style="dim",
        padding=(0, 1),
        title=title or None,
        title_style="bold",
    )
    table.add_column("Name", style="bold", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Detail")

    status_styles = {
        "done": "green",
        "success": "green",
        "ok": "green",
        "error": "red",
        "failed": "red",
        "running": "yellow",
        "pending": "dim",
        "skipped": "dim",
    }

    for name, status, detail in rows:
        style = status_styles.get(status.lower(), "")
        styled_status = f"[{style}]{status}[/{style}]" if style else status
        table.add_row(name, styled_status, detail)

    _console.print(table)


def cost_table(
    label: str,
    total_parts: dict[str, str],
    model_rows: list[tuple[str, str, str, str]] | None = None,
) -> None:
    """Print a cost summary panel.

    Args:
        label: Title for the panel (e.g. "Iteration 1 totals").
        total_parts: Top-level metrics as key→value (Duration, Cost, Tokens, etc.).
        model_rows: Optional per-model breakdown as (model, in, out, cost) tuples.
    """
    parts_text = "  ".join(f"[bold]{k}:[/bold] {v}" for k, v in total_parts.items())
    content = parts_text

    if model_rows:
        table = Table(show_header=True, border_style="dim", padding=(0, 1))
        table.add_column("Model", style="bold", no_wrap=True)
        table.add_column("In", justify="right")
        table.add_column("Out", justify="right")
        table.add_column("Cost", justify="right")
        for row in model_rows:
            table.add_row(*row)
        # Render table to string so we can combine with the top line
        from io import StringIO
        buf = StringIO()
        temp = Console(file=buf, width=_console.width)
        temp.print(parts_text)
        temp.print(table)
        content = buf.getvalue().rstrip()
        _console.print(Panel(content, title=label, border_style="dim", padding=(0, 1)))
        return

    _console.print(Panel(content, title=label, border_style="dim", padding=(0, 1)))


def operations_table(operations: list) -> None:
    """Print a detailed per-operation cost table.

    Args:
        operations: List of OperationCost objects or dicts.
    """
    if not operations:
        return

    table = Table(show_header=True, border_style="dim", padding=(0, 1))
    table.add_column("Operation", style="bold", no_wrap=True)
    table.add_column("Model", style="dim")
    table.add_column("In Tokens", justify="right")
    table.add_column("Out Tokens", justify="right")
    table.add_column("Cache Read", justify="right")
    table.add_column("Cost (USD)", justify="right", style="green")

    for op in operations:
        if hasattr(op, "name"):
            name = op.name
            model = op.model
            in_t = op.input_tokens
            out_t = op.output_tokens
            cache = op.cache_read_tokens
            cost = op.cost_usd
        else:
            name = op.get("name", "")
            model = op.get("model", "")
            in_t = op.get("input_tokens", 0)
            out_t = op.get("output_tokens", 0)
            cache = op.get("cache_read_tokens", 0)
            cost = op.get("cost_usd", 0.0)

        # Clean model name for display
        display_model = (
            model.replace("claude-3-5-", "")
            .replace("claude-3-", "")
            .replace("-20241022", "")
            .replace("-20240229", "")
        )

        table.add_row(
            name,
            display_model,
            f"{in_t:,}" if in_t else "0",
            f"{out_t:,}" if out_t else "0",
            f"{cache:,}" if cache else "0",
            f"${cost:.4f}",
        )

    _console.print(table)


# ── panels ────────────────────────────────────────────────────────────


def panel(
    content: str,
    title: str = "",
    style: str = "cyan",
) -> None:
    """Print a bordered panel with optional title."""
    _console.print(Panel(content, title=title or None, border_style=style, padding=(0, 2)))