"""`archon prove` — bootstrap a workspace and run the loop on a single statement."""

from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer

from archon import log
from archon.types import Stage


class ProveCommand:
    """Creates a fresh workspace, runs `archon init`, then `archon loop`."""

    def __init__(
        self,
        statement: str,
        project_path: str | None,
        *,
        max_iterations: int = 10,
        max_parallel: int = 8,
        stage: Stage | None = None,
        parallel: bool = True,
        verbose_logs: bool = False,
        no_review: bool = False,
        dry_run: bool = False,
    ) -> None:
        self.statement = statement
        self.project_path = project_path
        self.max_iterations = max_iterations
        self.max_parallel = max_parallel
        self.stage = stage
        self.parallel = parallel
        self.verbose_logs = verbose_logs
        self.no_review = no_review
        self.dry_run = dry_run

    def run(self) -> None:
        log.header("archon prove")
        workspace = self._create_workspace()
        self._init_project(workspace)
        self._run_loop(workspace)
        log.success(f"Proof process finished for: {workspace.name}")

    # ── private ────────────────────────────────────────────────────────

    def _create_workspace(self) -> Path:
        if not self.project_path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            clean_words = re.sub(r'[^a-zA-Z0-9\s]', '', self.statement).split()[:5]
            slug = "_".join(clean_words).lower()
            workspace = Path.cwd() / f"proof_{slug}_{timestamp}"
            workspace.mkdir(parents=True, exist_ok=True)
        else:
            workspace = Path(self.project_path).resolve()

        if workspace.exists() and any(workspace.iterdir()):
            log.error(
                f"Provided path {workspace} is not empty. "
                "Please provide an empty or non-existent directory.",
            )
            raise typer.Exit(1)

        workspace.mkdir(parents=True, exist_ok=True)
        statement_file = workspace / "statement.md"
        statement_file.write_text(self.statement, encoding="utf-8")
        log.success(f"Created workspace at {workspace}")
        log.step("Saved statement to statement.md")
        return workspace

    def _init_project(self, workspace: Path) -> None:
        log.phase(1, "Running archon init")
        try:
            subprocess.run(["archon", "init", str(workspace)], check=True)
        except subprocess.CalledProcessError:
            log.error("Failed during archon init. Aborting.")
            raise typer.Exit(1)

    def _run_loop(self, workspace: Path) -> None:
        log.phase(2, "Starting archon loop")
        cmd = self._build_loop_cmd(workspace)
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError:
            log.error("Failed during archon loop.")
            raise typer.Exit(1)

    def _build_loop_cmd(self, workspace: Path) -> list[str]:
        cmd = [
            "archon", "loop", str(workspace),
            "--max-iterations", str(self.max_iterations),
            "--max-parallel", str(self.max_parallel),
        ]
        if self.stage:
            cmd.extend(["--stage", self.stage.value])
        if not self.parallel:
            cmd.append("--serial")
        if self.verbose_logs:
            cmd.append("--verbose-logs")
        if self.no_review:
            cmd.append("--no-review")
        if self.dry_run:
            cmd.append("--dry-run")
        return cmd


def prove(
    statement: str = typer.Argument(
        ...,
        help="The mathematical statement you want to prove "
             "(e.g., 'For all x, y in R, (x+y)^2 = ...')",
    ),
    project_path: str = typer.Argument(
        None,
        help="Path where you want to create the proof workspace. "
             "Otherwise, a new directory named `proof_<slug>_<timestamp>` will "
             "be created in the current directory.",
    ),
    max_iterations: int = typer.Option(
        10, "--max-iterations", "-m", help="Max plan→prover→review cycles.",
    ),
    max_parallel: int = typer.Option(
        8, "--max-parallel", help="Max concurrent provers in parallel mode.",
    ),
    stage: Optional[Stage] = typer.Option(
        None, "--stage", "-s",
        help="Force a stage instead of reading from PROGRESS.md.",
    ),
    parallel: bool = typer.Option(
        True, "--parallel/--serial",
        help="Run provers in parallel (one per file) or serially.",
    ),
    verbose_logs: bool = typer.Option(
        False, "--verbose-logs",
        help="Save raw Claude stream events to .raw.jsonl.",
    ),
    no_review: bool = typer.Option(
        False, "--no-review",
        help="Skip review phase after each iteration.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Print prompts without launching Claude.",
    ),
) -> None:
    """Launch a proof loop for a given statement."""
    ProveCommand(
        statement,
        project_path,
        max_iterations=max_iterations,
        max_parallel=max_parallel,
        stage=stage,
        parallel=parallel,
        verbose_logs=verbose_logs,
        no_review=no_review,
        dry_run=dry_run,
    ).run()
