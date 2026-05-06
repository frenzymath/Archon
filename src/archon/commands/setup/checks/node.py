"""Install Node.js 18+ via nvm (no sudo)."""

from __future__ import annotations

from pathlib import Path

from archon import log

from ..shell import has, run, run_shell, source_nvm
from .base import DependencyCheck


_REQUIRED_MAJOR = 18


class NodeCheck(DependencyCheck):
    name = "Node.js"

    def run(self) -> bool:
        source_nvm()

        if has("node") and has("npm"):
            major, version_str = self._node_major()
            if major >= _REQUIRED_MAJOR:
                log.success(f"Node.js: v{version_str}")
                return True
            log.warn(
                f"Node.js {version_str} is too old "
                f"(need {_REQUIRED_MAJOR}+), upgrading via nvm...",
            )

        nvm_sh = Path.home() / ".nvm" / "nvm.sh"
        if not nvm_sh.exists() and not self._install_nvm():
            return False

        log.step("Installing Node.js via nvm...")
        r = run_shell(
            f'source "{nvm_sh}" && nvm install --lts && nvm use --lts',
        )
        if r.returncode != 0:
            log.error(f"Node.js installation via nvm failed: {r.stderr.strip()}")
            log.step("Install manually: https://nodejs.org/")
            return False

        source_nvm()
        if has("node") and has("npm"):
            _, version_str = self._node_major()
            log.success(f"Node.js installed: v{version_str}")
            return True

        log.error("Node.js installation succeeded but binaries not found in PATH")
        log.step('Try: source ~/.nvm/nvm.sh && nvm use --lts')
        return False

    # ── private ────────────────────────────────────────────────────────

    def _node_major(self) -> tuple[int, str]:
        r = run(["node", "-v"])
        version_str = (r.stdout or "").strip().lstrip("v")
        try:
            major = int(version_str.split(".")[0])
        except (ValueError, IndexError):
            major = 0
        return major, version_str

    def _install_nvm(self) -> bool:
        log.step("Installing nvm (Node Version Manager, to ~/.nvm, no sudo)...")
        r = run_shell(
            "curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.4/install.sh | bash",
        )
        if r.returncode != 0:
            log.error("nvm installation failed")
            log.step("Install manually: https://github.com/nvm-sh/nvm")
            return False
        log.success("nvm installed")
        return True
