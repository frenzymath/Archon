"""Typer-decorated `init` entry point."""

from __future__ import annotations

import typer

from archon.agent import DEFAULT_MODEL

from .command import InitCommand


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
    harness: str = typer.Option(
        None, "--harness",
        help="Engine for the loop's roles (plan/prover/review): "
             "'claude-code' (default), 'codex-gpt' (Codex CLI + GPT-5.5, "
             "native ~/.codex login), or 'mixed' (pick per role, "
             "interactive). Omit to be asked at init time.",
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
    InitCommand(project_path, force=force, model=model, harness=harness).run()
