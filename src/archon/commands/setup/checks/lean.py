"""Install/verify the Lean toolchain (elan / lean / lake)."""

from __future__ import annotations

import os
from pathlib import Path

import typer

from archon import log

from ..shell import has, run, run_shell, version
from .base import DependencyCheck


class LeanToolchainCheck(DependencyCheck):
    """Installs elan via Homebrew (if available) or the official no-sudo
    installer script.
    """

    name = "Lean toolchain"

    def run(self) -> bool:
        elan_bin = Path.home() / ".elan" / "bin"
        self._prepend_path(elan_bin)

        if self._all_tools_present():
            return True

        if not self._install_elan():
            return False

        self._prepend_path(elan_bin)

        if self._all_tools_present(label="installed"):
            return True

        log.error("Lean toolchain installation incomplete")
        log.step("Install manually: curl https://elan.lean-lang.org/elan-init.sh -sSf | sh")
        log.step('Then add to PATH: export PATH="$HOME/.elan/bin:$PATH"')
        return False

    # ── private ────────────────────────────────────────────────────────

    def _prepend_path(self, elan_bin: Path) -> None:
        if elan_bin.is_dir() and str(elan_bin) not in os.environ.get("PATH", ""):
            os.environ["PATH"] = f"{elan_bin}{os.pathsep}{os.environ['PATH']}"

    def _all_tools_present(self, *, label: str = "") -> bool:
        ok = True
        for tool in ("elan", "lean", "lake"):
            if has(tool):
                suffix = f" {label}" if label else ""
                log.success(f"{tool}{suffix}: {version([tool, '--version'])}")
            else:
                if label:
                    log.warn(f"{tool} still not found after install")
                ok = False
        return ok

    def _install_elan(self) -> bool:
        # Installing elan is either via Homebrew (no sudo) or via the
        # official installer script that writes to ~/.elan (also no
        # sudo). Neither needs the sudo gate, but the script invocation
        # still gets a confirmation prompt unless the installer is in
        # --yes mode.
        if has("brew"):
            log.step("Installing elan via Homebrew...")
            r = run(["brew", "install", "elan-init"])
            if r.returncode == 0:
                run(["elan", "default", "stable"])
            return True

        installer_cmd = (
            "curl https://elan.lean-lang.org/elan-init.sh -sSf | sh -s -- "
            "-y --default-toolchain stable"
        )
        log.warn("Archon is about to run the official elan installer:")
        log.step(f"  {installer_cmd}")
        log.step("This installs to ~/.elan — no sudo required.")
        if self.installer.sudo_mode == "ask":
            proceed = typer.confirm("  Run it now?", default=True)
        else:
            proceed = self.installer.sudo_mode == "yes"
        if not proceed:
            log.step(
                "Skipping elan install. See: https://lean-lang.org/lean4/doc/quickstart.html",
            )
            return False
        run_shell(installer_cmd)
        return True
