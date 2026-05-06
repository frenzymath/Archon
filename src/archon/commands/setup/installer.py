"""Package-manager abstraction with a configurable sudo policy.

Wraps the apt/brew/dnf/pacman differences so individual checks can ask
for a package by canonical name (or by per-PM bundle) without caring
which one is available. The sudo gate is centralized so `--yes` /
`--no-sudo` semantics live in one place.
"""

from __future__ import annotations

from typing import Literal

import typer

from archon import log

from .shell import has, run


SudoMode = Literal["ask", "yes", "no"]


class PackageInstaller:
    """Detects the local package manager and installs packages under a sudo policy."""

    def __init__(self, sudo_mode: SudoMode = "ask") -> None:
        self.sudo_mode = sudo_mode

    # ── public API ─────────────────────────────────────────────────────

    def install(self, pkg: str, install_urls: dict[str, str] | None = None) -> bool:
        """Install a single canonically-named package.

        Returns True if the install was attempted (does NOT guarantee
        the binary is now available — callers should re-check with
        `has()`).
        """
        pm = self._detect()
        if pm is None:
            log.warn(f"No supported package manager found for installing '{pkg}'.")
            self._print_manual_install(pkg, install_urls)
            return False

        name, base_cmd = pm
        cmd = base_cmd + [pkg]
        return self._run_install(name, cmd, pkg, install_urls)

    def install_bundle(
        self,
        label: str,
        packages_by_pm: dict[str, list[str]],
        install_urls: dict[str, str] | None = None,
    ) -> bool:
        """Install a bundle whose package names differ across PMs.

        `packages_by_pm` maps PM name ("brew", "apt-get", "dnf",
        "pacman") to its package list. Useful where Debian calls a lib
        `libgraphviz-dev` but Fedora calls it `graphviz-devel`.
        """
        pm = self._detect()
        if pm is None:
            log.warn(f"No supported package manager found for installing '{label}'.")
            self._print_urls(install_urls)
            return False

        name, base_cmd = pm
        pkgs = packages_by_pm.get(name)
        if not pkgs:
            log.warn(f"No package list defined for {name} when installing '{label}'.")
            self._print_urls(install_urls)
            return False

        cmd = base_cmd + pkgs
        return self._run_install(name, cmd, label, install_urls)

    def confirm_sudo(self, pkg: str, cmd: list[str]) -> bool:
        """Public entry to the sudo gate (used by checks that build their
        own command — e.g. the elan installer script).
        """
        return self._ask_sudo(pkg, cmd)

    # ── private ────────────────────────────────────────────────────────

    def _detect(self) -> tuple[str, list[str]] | None:
        """Return (name, install-cmd-prefix) for the first PM found."""
        if has("brew"):
            # Homebrew doesn't need (and refuses) sudo, so we special-case it.
            return ("brew", ["brew", "install"])
        if has("apt-get"):
            return ("apt-get", ["sudo", "apt-get", "install", "-y"])
        if has("dnf"):
            return ("dnf", ["sudo", "dnf", "install", "-y"])
        if has("pacman"):
            return ("pacman", ["sudo", "pacman", "-S", "--noconfirm"])
        return None

    def _ask_sudo(self, pkg: str, cmd: list[str]) -> bool:
        """Return True if we should run a sudo command for this package."""
        if cmd and cmd[0] != "sudo":
            # Non-sudo command (e.g. brew). No permission needed.
            return True
        if self.sudo_mode == "yes":
            return True
        if self.sudo_mode == "no":
            return False
        log.warn(f"Installing '{pkg}' requires sudo: {' '.join(cmd)}")
        return typer.confirm("  Run this command now?", default=False)

    def _print_urls(self, install_urls: dict[str, str] | None) -> None:
        if not install_urls:
            return
        for k, v in install_urls.items():
            log.step(f"  {k}: {v}")

    def _print_manual_install(
        self, pkg: str, install_urls: dict[str, str] | None,
    ) -> None:
        log.step(f"Skipping auto-install of '{pkg}'.")
        log.step(
            "To install manually, run one of the following "
            "(whichever matches your OS):",
        )
        log.step(f"  macOS (Homebrew):  brew install {pkg}")
        log.step(f"  Debian/Ubuntu:     sudo apt-get install -y {pkg}")
        log.step(f"  Fedora/RHEL:       sudo dnf install -y {pkg}")
        log.step(f"  Arch Linux:        sudo pacman -S --noconfirm {pkg}")
        self._print_urls(install_urls)

    def _run_install(
        self,
        pm_name: str,
        cmd: list[str],
        label: str,
        install_urls: dict[str, str] | None,
    ) -> bool:
        # apt-get needs `update` first to avoid stale indexes; do it
        # under the same sudo policy as the install itself.
        if pm_name == "apt-get":
            update_cmd = ["sudo", "apt-get", "update", "-qq"]
            if self._ask_sudo(f"apt update (prerequisite for {label})", update_cmd):
                run(update_cmd)
            else:
                log.step(
                    "Skipping apt-get update — the install may fail on a stale index.",
                )

        if not self._ask_sudo(label, cmd):
            log.step(
                f"Skipping auto-install of '{label}'. To install manually on your system:",
            )
            log.step(f"  {' '.join(cmd)}")
            self._print_urls(install_urls)
            return False

        log.step(f"Running: {' '.join(cmd)}")
        r = run(cmd)
        if r.returncode != 0:
            log.warn(f"Install command failed: {(r.stderr or r.stdout).strip()}")
            return False
        return True
