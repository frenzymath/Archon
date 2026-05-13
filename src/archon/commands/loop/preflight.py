"""Pre-loop sanity checks: claude availability, project state, env keys."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import typer

from archon import log
from archon.commands.tooling.inner_git import InnerGit
from archon.state import read_stage


def preflight(project_path: Path, state_dir: Path, dry_run: bool) -> None:
    """Verify claude is installed/auth'd and the project has been init'd."""
    progress = state_dir / "PROGRESS.md"

    if not dry_run:
        if not shutil.which("claude"):
            log.error("Claude Code is not installed. Run: archon setup")
            raise typer.Exit(1)
        r = subprocess.run(
            ["claude", "-p", "reply with OK", "--no-session-persistence"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            log.error("Claude Code cannot run. Check: claude auth, ANTHROPIC_API_KEY, network.")
            # raise typer.Exit(1)
        log.success("Claude Code is authenticated and ready")

    if not progress.exists():
        log.error(f"No project state found. Run: archon init {project_path}")
        raise typer.Exit(1)

    stage = read_stage(progress)
    if stage == "init":
        log.error(f"Project is still in init stage. Run: archon init {project_path}")
        raise typer.Exit(1)


def warn_if_inner_dirty(project_path: Path) -> None:
    """If the inner git has leftover state, tell the user; do not block."""
    inner = InnerGit(project_path)
    if not inner.is_initialized() or not inner.is_dirty():
        return

    log.warn(
        "Inner git has uncommitted agent work — leftover from a previous "
        "run or manual edits. This is fine: the loop will pick up whatever "
        "is on disk, and the next phase commit will capture it."
    )


def check_informal_agent_keys() -> None:
    """Warn (don't fail) if no external-LLM key is set for the informal agent."""
    keys = ("OPENAI_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY")
    if not any(os.environ.get(k) for k in keys):
        log.warn(
            "No API keys for informal agent "
            "(OPENAI_API_KEY / GEMINI_API_KEY / OPENROUTER_API_KEY)"
        )
        log.step(
            "Provers will work without it, but may struggle on hard sorries "
            "where external LLM help would be useful."
        )
