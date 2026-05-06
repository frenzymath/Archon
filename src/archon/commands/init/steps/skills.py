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
        self._copy_informal_agent()

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

    def _copy_informal_agent(self) -> None:
        ctx = self.ctx
        tools_dir = ctx.project_path / ".claude" / "tools"
        tools_dir.mkdir(parents=True, exist_ok=True)
        agent_src = data_path("tools/informal_agent.py")
        agent_dst = tools_dir / "archon-informal-agent.py"
        if agent_src.exists():
            copy_file(agent_src, agent_dst, overwrite=True)
            log.success("Informal agent copied to .claude/tools/")
