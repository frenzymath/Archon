"""Typer-decorated `setup` entry point."""

from __future__ import annotations

import typer

from archon import log

from .command import SetupCommand


def setup(
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Accept all sudo-requiring installs non-interactively. "
        "Only use in containers/CI where you trust the environment.",
    ),
    no_sudo: bool = typer.Option(
        False, "--no-sudo",
        help="Never run sudo. For any dependency that would require it, "
        "print manual install instructions and continue.",
    ),
    skip_blueprint: bool = typer.Option(
        False, "--skip-blueprint",
        help="Skip installing blueprint-related dependencies (graphviz, TeX, leanblueprint). "
        "Use this if you don't need the LaTeX blueprint feature.",
    ),
) -> None:
    """Install system-level dependencies.

    Checks and installs (without silent sudo) git, Python 3.10+, curl,
    elan/lean/lake, uv, ripgrep, Claude Code, Node.js (via nvm), dashboard
    npm dependencies, and verifies external-model API keys.

    Also installs leanblueprint and its system-level prerequisites
    (graphviz + dev headers, TeX Live, ghostscript, dvisvgm, pdf2svg,
    pdfcrop), unless --skip-blueprint is passed.

    By default, prompts before any sudo command. Use --yes to auto-accept
    (containers/CI) or --no-sudo to print manual instructions instead.

    tmux is NOT installed — Archon's parallel prover uses ProcessPoolExecutor
    and does not need tmux.
    """
    if yes and no_sudo:
        log.error("--yes and --no-sudo are mutually exclusive.")
        raise typer.Exit(1)

    if yes:
        sudo_mode = "yes"
        log.warn("Running in --yes mode: sudo commands will NOT be confirmed.")
    elif no_sudo:
        sudo_mode = "no"
        log.info(
            "Running in --no-sudo mode: manual instructions will be printed for packaged deps.",
        )
    else:
        sudo_mode = "ask"

    SetupCommand(sudo_mode=sudo_mode, skip_blueprint=skip_blueprint).run()
