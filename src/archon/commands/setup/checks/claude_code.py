"""Install Claude Code (no-sudo install to ~/.local/bin)."""

from __future__ import annotations

import os
from pathlib import Path

from archon import log

from ..shell import has, run_shell, version
from .base import DependencyCheck


class ClaudeCodeCheck(DependencyCheck):
    name = "Claude Code"

    def run(self) -> bool:
        if has("claude"):
            log.success(
                f"Claude Code: {version(['claude', '--version'])} (update: claude update)",
            )
            return True

        log.step(
            "Installing Claude Code (to ~/.local/bin, no sudo, may take a few minutes)...",
        )
        run_shell("curl -fsSL https://claude.ai/install.sh | bash")
        os.environ["PATH"] = (
            f"{Path.home() / '.local' / 'bin'}{os.pathsep}{os.environ['PATH']}"
        )
        if has("claude"):
            log.success(f"Claude Code installed: {version(['claude', '--version'])}")
            return True
        log.error("Claude Code installation failed")
        log.step("Install manually: https://code.claude.com/docs/en/overview")
        return False
