"""Re-init detection and merge controller.

Handles the case where `archon init` is run on an already-initialized
project: detect the layout, prompt the user (keep / merge / overwrite /
abort), and on `merge` invoke Claude to walk through bundled-vs-local
file diffs.
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import typer

from archon import log
from archon.agent import ClaudeAgent, DEFAULT_MODEL

from .utils import data_path, has, parse_stage


class ReinitController:
    """Detects existing Archon state and resolves the user's re-init mode."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir

    def detect(self) -> dict:
        info = {
            "exists": self.state_dir.is_dir(),
            "has_progress": False,
            "has_prompts": False,
            "prompts_are_symlinks": False,
            "stage": "init",
            "version": "unknown",
        }
        if not info["exists"]:
            return info
        progress = self.state_dir / "PROGRESS.md"
        info["has_progress"] = progress.exists()
        if info["has_progress"]:
            info["stage"] = parse_stage(progress)

        prompts_dir = self.state_dir / "prompts"
        if prompts_dir.is_dir():
            info["has_prompts"] = True
            md_files = list(prompts_dir.glob("*.md"))
            if md_files and any(f.is_symlink() for f in md_files):
                info["prompts_are_symlinks"] = True
                info["version"] = "legacy-symlink"
            elif md_files:
                info["version"] = "current-copy"
        return info

    def prompt_mode(self, info: dict) -> str:
        log.warn("This project has already been initialized with Archon.")
        log.key_value({
            "Detected layout": info["version"],
            "Current stage": info["stage"],
            "Prompts are symlinks": "yes" if info["prompts_are_symlinks"] else "no",
        })

        if info["prompts_are_symlinks"]:
            log.step(
                "Detected the legacy symlink-based layout. The new CLI copies prompts "
                "into .archon/prompts/ instead of symlinking. Re-initializing directly "
                "would break the old symlinks."
            )

        typer.echo("")
        typer.echo("How would you like to proceed?")
        typer.echo("  [k] keep       — preserve prompts/CLAUDE.md; refresh MCP, skills, hooks, version stamp")
        typer.echo("  [m] merge      — compare each file and let Claude help reconcile (recommended)")
        typer.echo("  [o] overwrite  — replace all Archon files with the bundled versions")
        typer.echo("  [a] abort      — cancel")
        typer.echo("")

        while True:
            choice = typer.prompt("Choice [k/m/o/a]", default="m").strip().lower()
            if choice in ("k", "keep"):
                return "keep"
            if choice in ("m", "merge"):
                return "merge"
            if choice in ("o", "overwrite"):
                if typer.confirm(
                    "This will overwrite local changes to .archon/prompts/ and "
                    ".archon/CLAUDE.md. Continue?",
                ):
                    return "overwrite"
            if choice in ("a", "abort"):
                return "abort"


class PromptMerger:
    """Stages bundled prompts and asks Claude to reconcile them with local edits."""

    def __init__(
        self,
        project_path: Path,
        state_dir: Path,
        *,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self.project_path = project_path
        self.state_dir = state_dir
        self.model = model

    def run(self) -> None:
        staging = self._stage_bundled_prompts()
        self._merge_with_claude(staging)

    def _stage_bundled_prompts(self) -> Path:
        staging = self.state_dir / ".archon-incoming"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)

        prompts_src = data_path("prompts")
        if prompts_src.exists():
            prompts_stage = staging / "prompts"
            prompts_stage.mkdir(exist_ok=True)
            for f in sorted(prompts_src.glob("*.md")):
                shutil.copy2(f, prompts_stage / f.name)

        # Note: bundled .claude/agents/*.md files were removed when the
        # subagents migrated to Python tool wrappers (.claude/tools/).
        # Nothing to stage here.

        template_dir = data_path("archon-template")
        if template_dir.exists():
            for name in ("CLAUDE.md",):
                src = template_dir / name
                if src.exists():
                    shutil.copy2(src, staging / name)
        return staging

    def _merge_with_claude(self, staging: Path) -> None:
        log.phase(0, "Reconciling local vs. bundled Archon files")
        log.step("Launching Claude Code to walk you through the differences file by file.")

        if not has("claude"):
            log.warn("Claude Code is not installed — falling back to a text-only diff summary.")
            self._print_diff_summary(staging)
            return

        project_path = self.state_dir.parent
        legacy_agents_dir = project_path / ".claude" / "agents"
        legacy_agents = [
            legacy_agents_dir / f"{name}.md"
            for name in ("analogy", "challenger", "refactor")
        ]
        legacy_agents_present = [p for p in legacy_agents if p.exists()]

        legacy_block = ""
        if legacy_agents_present:
            legacy_list = "\n            ".join(f"- {p}" for p in legacy_agents_present)
            legacy_block = textwrap.dedent(f"""

            Legacy cleanup — these Markdown subagent files predate the migration to
            Python tool wrappers (.claude/tools/archon-<role>-agent.py). Delete them
            so the plan agent doesn't pick them up via the Agent tool by mistake:
            {legacy_list}
            """)

        prompt = textwrap.dedent(f"""\
            You are helping the user reconcile their existing Archon setup with the newer bundled
            versions. This is a merge session, NOT a normal init.

            Paths:
            - Existing local prompts (user may have edited): {self.state_dir}/prompts/
            - Existing local CLAUDE.md:                     {self.state_dir}/CLAUDE.md
            - Incoming (bundled, new version):              {staging}/

            Path mapping for incoming files:
            - {staging}/prompts/<f>.md  ↔  {self.state_dir}/prompts/<f>.md
            - {staging}/CLAUDE.md       ↔  {self.state_dir}/CLAUDE.md

            For every file under {staging}, compare against the corresponding existing local file
            per the mapping above. For each file that differs, show a concise summary of what
            changed, then ask the user to choose:
            [L] keep local   [N] take new   [M] merge manually

            Rules:
            - Never edit .lean files.
            - Never touch {self.state_dir}/PROGRESS.md, task_pending.md, task_done.md,
                USER_HINTS.md, or REFACTOR_DIRECTIVE.md.
            - Only reconcile the files listed in the path mapping above.
            - When done, delete {staging} and report: "Merged N files, kept M files."
            """) + legacy_block

        ClaudeAgent(model=self.model, role="init-merge").run_interactive(
            prompt, cwd=project_path,                                    
        )
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    def _print_diff_summary(self, staging: Path) -> None:
        log.step("Files that differ between your local setup and the bundled version:")
        differs = 0
        for incoming in staging.rglob("*"):
            if not incoming.is_file():
                continue
            rel = incoming.relative_to(staging)
            local = self.state_dir / rel
            if not local.exists():
                log.warn(f"  + {rel} (new in bundled version)")
                differs += 1
            elif local.read_bytes() != incoming.read_bytes():
                log.warn(f"  ~ {rel} (differs)")
                differs += 1
        if differs == 0:
            log.success("No differences — your local setup matches the bundled version.")
