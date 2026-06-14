"""InitCommand orchestrator.

Wires re-init detection + the deterministic + semantic step sequence
into one runnable unit. The Typer entry point in :mod:`.entry` builds
the command and calls `.run()`.
"""

from __future__ import annotations

import os
from pathlib import Path

import typer

from archon import log
from archon.agent import ClaudeBackend, DEFAULT_MODEL
from archon.commands.tooling.version import warn_if_mismatch

from .context import InitContext
from .reinit import PromptMerger, ReinitController
from .steps import (
    BootstrapStep,
    CopyPromptsStep,
    DisableConflictingPluginsStep,
    EnvAndConfigStep,
    GitHooksStep,
    InnerGitStep,
    LeanLspMcpStep,
    ReportProtectedStep,
    SemanticPassStep,
    SkillsStep,
    StateDirStep,
    VersionStampStep,
)
from .utils import fail_permission, has


class InitCommand:
    """Orchestrates one full `archon init` invocation."""

    def __init__(
        self,
        project_path: str | None,
        *,
        force: bool = False,
        model: str = DEFAULT_MODEL,
        backend: ClaudeBackend | None = None,
        harness: str | None = None,
    ) -> None:
        self.project_path_arg = project_path
        self.force = force
        self.model = model
        self.backend = backend
        self.harness = harness
        self.ctx: InitContext | None = None

    def run(self) -> None:
        log.header("archon init")
        resolved = self._resolve_or_create_project_dir()

        # Fail fast if we can't write inside the project dir — otherwise
        # the first attempt to create .archon/ bombs out mid-phase with a
        # raw Python traceback.
        if not os.access(resolved, os.W_OK):
            fail_permission(resolved, None)

        state_dir = resolved / ".archon"
        log.key_value({
            "Project": str(resolved),
            "State dir": str(state_dir),
        })

        warn_if_mismatch(resolved)

        if not has("claude"):
            log.warn(
                "Claude Code is not installed. Claude-backed harnesses will "
                "fail, but Codex-backed harnesses can still run."
            )

        self.ctx = InitContext(
            project_path=resolved,
            state_dir=state_dir,
            fresh=True,
            model=self.model,
            backend=self.backend or ClaudeBackend(),
            harness=self.harness,
        )

        mode = self._resolve_reinit_mode()
        if mode == "abort":
            log.info("Aborted by user — no changes made.")
            raise typer.Exit(0)

        # Anything other than a clean "fresh" init means there's already a
        # .archon/ on disk (possibly with legacy symlinks). The non-fresh
        # branch in CopyPromptsStep handles symlinks correctly by unlinking
        # before copy; the fresh branch follows them and crashes with
        # SameFileError. Pin ctx.fresh to False for keep/merge/overwrite so
        # everyone takes the symlink-aware path.
        if mode != "fresh":
            self.ctx.fresh = False

        # Prompt files should be force-replaced on a fresh install OR when
        # the user explicitly chose "overwrite" (or --force). "merge" and
        # "keep" deliberately preserve local edits, so they don't set this.
        if mode in ("fresh", "overwrite"):
            self.ctx.overwrite = True

        if mode == "keep":
            self._run_keep_only()
            return

        if mode == "merge":
            PromptMerger(resolved, state_dir, model=self.model, backend=self.backend or ClaudeBackend()).run()

        self._run_full_init()

    # ── private ────────────────────────────────────────────────────────

    def _resolve_or_create_project_dir(self) -> Path:
        """Resolve the project path argument, creating the directory if missing."""
        if self.project_path_arg is None:
            log.info("No project path specified")
            log.step("Enter a name to create a new project, or Ctrl-C and re-run.")
            name = typer.prompt("  Project name")
            if not name:
                log.error("No project name entered")
                raise typer.Exit(1)
            resolved = Path.cwd() / name
            try:
                resolved.mkdir(parents=True, exist_ok=True)
            except PermissionError as e:
                fail_permission(resolved, e)
            log.success(f"Created project at {resolved}")
            return resolved

        resolved = Path(self.project_path_arg).resolve()
        if not resolved.exists():
            try:
                resolved.mkdir(parents=True, exist_ok=True)
            except PermissionError as e:
                fail_permission(resolved, e)
            log.success(f"Created directory {resolved}")
        return resolved

    def _resolve_reinit_mode(self) -> str:
        """Return one of 'fresh', 'keep', 'merge', 'overwrite', 'abort'."""
        ctx = self.ctx
        controller = ReinitController(ctx.state_dir)
        info = controller.detect()
        if not (info["exists"] and info["has_progress"]):
            return "fresh"
        if self.force:
            log.warn("--force passed: overwriting existing Archon setup")
            return "overwrite"
        return controller.prompt_mode(info)

    def _run_keep_only(self) -> None:
        """Verify-only path — keep existing setup, refresh registrations."""
        log.info("Keeping existing setup. Verifying MCP / plugin registration only.")
        for step_cls in (
            LeanLspMcpStep, SkillsStep, DisableConflictingPluginsStep,
            ReportProtectedStep, EnvAndConfigStep, InnerGitStep,
            GitHooksStep, VersionStampStep,
        ):
            step_cls(self.ctx).run()
        log.success("Verification complete.")

    def _run_full_init(self) -> None:
        """Deterministic setup -> optional semantic pass -> final stamps."""
        ctx = self.ctx

        for step_cls in (
            StateDirStep, CopyPromptsStep, BootstrapStep,
            LeanLspMcpStep, SkillsStep, DisableConflictingPluginsStep,
            EnvAndConfigStep,
        ):
            step_cls(ctx).run()

        if ctx.fresh:
            SemanticPassStep(ctx).run()
        else:
            log.success("Merge-based re-init complete.")
            log.step(f"Next: archon loop {ctx.project_path}")

        # Always show the protected-declarations summary, then inner-git /
        # hook install / version stamp (in that order so the inner-git
        # commit captures the freshly-written .env and config.json, and
        # the hook is installed against the git-dir that was just created).
        for step_cls in (
            ReportProtectedStep, InnerGitStep, GitHooksStep, VersionStampStep,
        ):
            step_cls(ctx).run()
