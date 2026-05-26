"""Typer-decorated `init` entry point."""

from __future__ import annotations
from pathlib import Path
from typing import Optional

import typer

from archon.agent import DEFAULT_MODEL

from .command import InitCommand
from archon.commands.tooling.project_config import load_project_config, resolve_claude_backend


def init(
    project_path: str = typer.Argument(
        None,
        help="Path to Lean project (directory containing lakefile.lean/toml). "
        "If omitted, prompts for a name and creates the project.",
    ),
    force: bool = typer.Option(
        False, "--force",
        help="Skip the re-init prompt and overwrite existing Archon files.",
    ),
    model: str = typer.Option(
        DEFAULT_MODEL, "--model", "-M",
        help="Model used for the interactive init pass. Anthropic: 'opus', "
             "'sonnet', 'haiku' or a full id. Non-Anthropic (requires "
             ".archon/.env credentials): 'kimi', 'deepseek'.",
    ),
    claude_backend: Optional[str] = typer.Option(
        None, "--claude-backend",
        help=(
            "How 'claude -p' is invoked for every headless agent run. "
            "'default': plain claude -p. "
            "'vscode': sets CLAUDE_CODE_ENTRYPOINT=claude-vscode. "
            "'desktop': sets CLAUDE_CODE_ENTRYPOINT=claude-desktop. "
            "(default from .archon/config.json loop.claude_backend or 'default')"
        ),
    ),
) -> None:
    """Initialize a new Archon project.

    Runs the deterministic bootstrap (lake init, Mathlib, blueprint, workspace
    skeletons) in Python, then hands off to Claude Code for the semantic pass
    only: reorganizing reference files, writing README/summary.md prose, and
    proposing initial objectives.

    [bold]Examples:[/bold]
      [cyan]archon init .[/cyan]
      [cyan]archon init /path/to/lean-project[/cyan]
    """
    project_config = load_project_config(Path(project_path))
    backend = resolve_claude_backend(project_config, cli_value=claude_backend)
    InitCommand(project_path, force=force, model=model, backend=backend).run()
