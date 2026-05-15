"""Install/verify the `uv` Python package manager (no-sudo install to ~/.local/bin)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from archon import log

from ..shell import ensure_path_in_rc, has, in_virtualenv, run, run_shell, version
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
            pip_cmd = [sys.executable, "-m", "pip", "install"]
            if not in_virtualenv():
                # `--user` is rejected by pip inside a venv.
                pip_cmd.append("--user")
            pip_cmd.append("uv")
            log.warn(f"Standalone installer failed, trying {' '.join(pip_cmd[2:])}...")
            run(pip_cmd)

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
