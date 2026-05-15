"""Install the Archon `lean4` skills bundle as a project-scoped Claude plugin."""

from __future__ import annotations

from pathlib import Path

import typer

from archon import log

from ..utils import copy_file, data_path, read_json, run
from .base import InitStep


class SkillsStep(InitStep):
    name = "Installing Archon skills"
    number = 5

    def run(self) -> None:
        ctx = self.ctx
        log.phase(self.number, self.name)

        home = Path.home()
        skills_dir = data_path("skills")
        plugin_json_path = skills_dir / "lean4" / ".claude-plugin" / "plugin.json"

        if not plugin_json_path.exists():
            log.error("Archon lean4 skills not found in package data")
            raise typer.Exit(1)

        (ctx.project_path / ".claude" / "skills").mkdir(parents=True, exist_ok=True)
        (ctx.project_path / ".claude" / "rules").mkdir(parents=True, exist_ok=True)

        self._register_marketplace(home, skills_dir)
        self._install_plugin(home)
        self._copy_archon_tools()
        self._cleanup_legacy_subagents()

    # ── private ────────────────────────────────────────────────────────

    def _register_marketplace(self, home: Path, skills_dir: Path) -> None:
        log.step("Registering archon-local marketplace")
        market_needs_update = True
        r = run(["claude", "plugin", "marketplace", "list"])
        if "archon-local" in (r.stdout or ""):
            known_path = home / ".claude" / "plugins" / "known_marketplaces.json"
            data = read_json(known_path)
            current = data.get("archon-local", {}).get("source", {}).get("path", "")
            if current == str(skills_dir):
                log.success("archon-local marketplace already up to date")
                market_needs_update = False
            else:
                log.warn(f"archon-local points to a stale path: {current}")
                run(["claude", "plugin", "marketplace", "remove", "archon-local"])

        if market_needs_update:
            r = run(["claude", "plugin", "marketplace", "add", str(skills_dir)])
            output = r.stdout + r.stderr
            if r.returncode == 0 or "already" in output.lower():
                log.success("Registered archon-local marketplace")
            else:
                log.error(f"Failed to register marketplace: {output.strip()}")
                raise typer.Exit(1)

    def _install_plugin(self, home: Path) -> None:
        ctx = self.ctx
        log.step("Installing lean4 plugin (project scope)")
        installed_json = home / ".claude" / "plugins" / "installed_plugins.json"
        installed_data = read_json(installed_json)
        installed_here = any(
            entry.get("projectPath") == str(ctx.project_path)
            for entry in installed_data.get("plugins", {}).get("lean4@archon-local", [])
        )
        if installed_here:
            log.success("lean4@archon-local already installed for this project")
            return

        r = run(
            ["claude", "plugin", "install", "lean4@archon-local",
             "--scope", "project"],
            cwd=ctx.project_path,
        )
        output = r.stdout + r.stderr
        if "success" in output.lower() or r.returncode == 0:
            log.success("lean4@archon-local installed")
        else:
            log.error(f"Failed to install lean4@archon-local: {output.strip()}")
            raise typer.Exit(1)

    # The single ``subagent_wrapper.py`` source is installed under one
    # filename per subagent role; the wrapper itself derives its role
    # from ``sys.argv[0]``. Keeping the list here (instead of in the
    # wrapper) means adding a new subagent is a one-line edit.
    _SUBAGENT_ROLES = (
        "refactor", "analogy", "challenger", "coordinator",
        "review-definition-correctness",
        "review-comment-hygiene",
        "review-blueprint-consistency",
        "review-design-choices",
        "review-mathlib-overlap",
    )
    _SUBAGENT_WRAPPER_STEM = "subagent_wrapper"

    def _copy_archon_tools(self) -> None:
        """Copy every Archon tool script into the project's .claude/tools/.

        Each script in our package's ``data/tools/`` becomes
        ``.claude/tools/archon-<stem-with-dashes>.py`` in the project. The
        sole exception is ``subagent_wrapper.py``: it's installed under
        one role-specific name per subagent (see ``_SUBAGENT_ROLES``)
        so Claude can keep invoking ``archon-<role>-agent.py`` while
        the underlying script is single-sourced.

        The plan agent's CLAUDE.md and prompt document the invocation
        patterns; Claude calls them via Bash.
        """
        ctx = self.ctx
        tools_src = data_path("tools")
        tools_dst = ctx.project_path / ".claude" / "tools"
        tools_dst.mkdir(parents=True, exist_ok=True)

        if not tools_src.is_dir():
            log.warn("Archon tools directory not found in package data")
            return

        for src in sorted(tools_src.glob("*.py")):
            if src.stem == self._SUBAGENT_WRAPPER_STEM:
                self._install_subagent_wrappers(src, tools_dst)
                continue
            # informal_agent.py -> archon-informal-agent.py
            stem = src.stem.replace("_", "-")
            dst = tools_dst / f"archon-{stem}.py"
            copy_file(src, dst, overwrite=True)
            log.success(f"Copied {dst.name}")

        # Older Archon installs created one file per role; the wrapper
        # source is now consolidated. Sweep any abandoned per-role file
        # that doesn't match our installed naming scheme so the project
        # doesn't carry stale logic. (We only remove files we know we
        # used to install — never anything else under .claude/tools/.)
        for stale in (
            "archon-refactor-wrapper.py",
            "archon-analogy-wrapper.py",
            "archon-challenger-wrapper.py",
        ):
            stale_path = tools_dst / stale
            if stale_path.is_file():
                stale_path.unlink()

    def _install_subagent_wrappers(self, src: Path, tools_dst: Path) -> None:
        """Install ``subagent_wrapper.py`` under one filename per role."""
        for role in self._SUBAGENT_ROLES:
            dst = tools_dst / f"archon-{role}-agent.py"
            copy_file(src, dst, overwrite=True)
            log.success(f"Copied {dst.name}")

    def _cleanup_legacy_subagents(self) -> None:
        """Remove pre-migration ``.claude/agents/{analogy,challenger,refactor}.md``.

        These were the Markdown-defined subagents replaced by Python tool
        wrappers. Leaving them in place creates a second invocation route
        (the Agent tool) that bypasses Archon's JSONL parser. We remove
        only those three filenames; any other user-defined ``.claude/
        agents/*.md`` is left alone.
        """
        agents_dir = self.ctx.project_path / ".claude" / "agents"
        if not agents_dir.is_dir():
            return
        for stem in ("analogy", "challenger", "refactor"):
            stale = agents_dir / f"{stem}.md"
            if stale.is_file() or stale.is_symlink():
                try:
                    stale.unlink()
                    log.success(f"Removed legacy .claude/agents/{stem}.md")
                except OSError as e:
                    log.warn(f"Could not remove {stale}: {e}")
