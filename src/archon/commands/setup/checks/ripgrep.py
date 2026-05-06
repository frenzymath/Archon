"""Install ripgrep (optional — used for fast code search)."""

from __future__ import annotations

from archon import log

from ..shell import has, version
from .base import DependencyCheck


class RipgrepCheck(DependencyCheck):
    name = "ripgrep"

    def run(self) -> bool:
        if has("rg"):
            log.success(f"ripgrep: {version(['rg', '--version'])}")
            return True

        log.step("ripgrep is optional (used for code search). Attempting install...")
        self.installer.install(
            "ripgrep", {"Manual": "https://github.com/BurntSushi/ripgrep"},
        )

        if has("rg"):
            log.success(f"ripgrep installed: {version(['rg', '--version'])}")
            return True
        log.warn("ripgrep not installed — some search tools will be slower.")
        return False
