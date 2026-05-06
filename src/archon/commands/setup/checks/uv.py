"""Install/verify the `uv` Python package manager (no-sudo install to ~/.local/bin)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from archon import log

from ..shell import ensure_path_in_rc, has, run, run_shell, version
from .base import DependencyCheck


class UvCheck(DependencyCheck):
    name = "uv"

    def run(self) -> bool:
        if has("uv"):
            log.success(f"uv: {version(['uv', '--version'])}")
            run(["uv", "self", "update"])
            return True

        log.step("Installing uv (to ~/.local/bin, no sudo)...")
        r = run_shell("curl -LsSf https://astral.sh/uv/install.sh | sh")
        if r.returncode != 0:
            log.warn("Standalone installer failed, trying pip --user...")
            run([sys.executable, "-m", "pip", "install", "--user", "uv"])

        os.environ["PATH"] = (
            f"{Path.home() / '.local' / 'bin'}{os.pathsep}{os.environ['PATH']}"
        )
        if has("uv"):
            log.success(f"uv installed: {version(['uv', '--version'])}")
            ensure_path_in_rc()
            return True
        log.error("uv installation failed")
        log.step("Install manually: https://docs.astral.sh/uv/getting-started/installation/")
        return False
