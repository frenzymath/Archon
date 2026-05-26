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
    if "init" in stage:
        log.error(f"Project is still in init stage. Run: archon init {project_path}")
        raise typer.Exit(1)


def warn_if_lake_unbuilt(project_path: Path) -> None:
    """Warn (don't fail) when the project looks unbuilt.

    A cold ``.lake/build`` is the most common cause of the LSP MCP
    server returning ``success: false`` on the prover's first query —
    which the model historically misread as "the tool doesn't exist"
    and fell back to running ``lean_goal`` as a shell command (which
    obviously fails). Surfacing this up front gives the user a chance
    to ``lake build`` before burning a prover round on a cold cache.

    Skipped silently when there's no ``lakefile.lean`` / ``lakefile.toml``
    (not a Lake project, nothing to warn about).
    """
    has_lake = (
        (project_path / "lakefile.lean").exists()
        or (project_path / "lakefile.toml").exists()
    )
    if not has_lake:
        return
    build_dir = project_path / ".lake" / "build"
    if build_dir.is_dir() and any(build_dir.iterdir()):
        return

    log.warn(
        "No .lake/build artifacts found. The Lean LSP MCP server "
        "may return success: false on its first call and the prover "
        "could waste effort interpreting that as a missing tool. "
        "Consider running `lake build` once before starting the loop."
    )


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
    keys = (
        "DEEPSEEK_API_KEY",
        "MOONSHOT_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
    )
    if not any(os.environ.get(k) for k in keys):
        log.warn(
            "No API keys for informal agent "
            "(DEEPSEEK_API_KEY / MOONSHOT_API_KEY / OPENROUTER_API_KEY / "
            "OPENAI_API_KEY / GEMINI_API_KEY)"
        )
        log.step(
            "Provers will work without it, but may struggle on hard sorries "
            "where external LLM help would be useful."
        )
