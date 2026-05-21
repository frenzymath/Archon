"""Disable globally-installed lean4-skills plugins for this project."""

from __future__ import annotations

from archon import log

from ..utils import find_global_lean4_plugins, run
from .base import InitStep


class DisableConflictingPluginsStep(InitStep):
    name = "Checking for conflicting global lean4-skills"
    number = 6

    def run(self) -> None:
        ctx = self.ctx
        log.phase(self.number, self.name)
        conflicting = find_global_lean4_plugins()
        if not conflicting:
            log.success("No conflicting global lean4-skills detected")
            return
        log.warn(f"Found {len(conflicting)} conflicting plugin(s) in global config")
        for name in conflicting:
            r = run(
                ["claude", "plugin", "disable", name, "--scope", "project"],
                cwd=ctx.project_path,
            )
            if r.returncode == 0:
                log.success(f"Disabled '{name}' for this project")
            else:
                log.warn(f"Could not auto-disable '{name}'")
