"""Probe for `git` and install via the system package manager if missing."""

from __future__ import annotations

from archon import log

from ..shell import has, version
from .base import DependencyCheck


class GitCheck(DependencyCheck):
    name = "git"

    def run(self) -> bool:
        if has("git"):
            log.success(f"git: {version(['git', '--version'])}")
            return True

        log.step("git not found, attempting install...")
        self.installer.install("git", {"Manual": "https://git-scm.com/downloads"})

        if has("git"):
            log.success(f"git installed: {version(['git', '--version'])}")
            return True
        log.error("git is not available — install it manually and re-run.")
        return False
