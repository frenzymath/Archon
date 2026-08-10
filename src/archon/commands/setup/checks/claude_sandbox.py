"""Install the Linux dependencies used by Claude Code's native sandbox."""

from __future__ import annotations

import sys

from archon import log

from ..shell import has
from .base import DependencyCheck


class ClaudeSandboxCheck(DependencyCheck):
    """``bubblewrap`` and ``socat`` for ``archon loop --safe`` on Linux."""

    name = "Claude Code sandbox"

    def run(self) -> bool:
        if sys.platform == "darwin":
            log.success("Claude Code sandbox: macOS Seatbelt available")
            return True
        if not sys.platform.startswith("linux"):
            log.warn(
                "Claude Code native sandbox is unavailable on this platform; "
                "use Linux, WSL2, macOS, or an external container for --safe."
            )
            return False
        if has("bwrap") and has("socat"):
            log.success("Claude Code sandbox: bubblewrap + socat available")
            return True

        log.step(
            "bubblewrap and socat enable `archon loop --safe`; "
            "attempting install..."
        )
        self.installer.install_bundle(
            "Claude Code sandbox (bubblewrap, socat)",
            {
                "apt-get": ["bubblewrap", "socat"],
                "dnf": ["bubblewrap", "socat"],
                "pacman": ["bubblewrap", "socat"],
            },
            install_urls={
                "Manual": "https://code.claude.com/docs/en/sandboxing",
            },
        )
        if has("bwrap") and has("socat"):
            log.success("Claude Code sandbox installed: bubblewrap + socat")
            return True
        log.warn(
            "Claude Code sandbox dependencies are incomplete; `archon loop "
            "--safe` will fail closed until bubblewrap and socat are installed."
        )
        return False
