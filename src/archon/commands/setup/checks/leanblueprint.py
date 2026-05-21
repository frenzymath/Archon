"""Install (or upgrade) the `leanblueprint` Python CLI."""

from __future__ import annotations

import sys

from archon import log

from ..shell import ensure_path_in_rc, has, in_virtualenv, run, version
from .base import DependencyCheck


class LeanBlueprintCheck(DependencyCheck):
    name = "leanblueprint"

    def run(self) -> bool:
        already = has("leanblueprint")
        action = "Upgrading" if already else "Installing"

        venv = in_virtualenv()
        cmd = [sys.executable, "-m", "pip", "install"]
        if not venv:
            # Outside a venv, install to the user's site-packages so we
            # don't need sudo. Inside a venv, `--user` is rejected by pip.
            cmd.append("--user")
        cmd.extend(["--upgrade", "leanblueprint"])

        target = "active virtualenv" if venv else "~/.local (pip --user)"
        log.step(f"{action} leanblueprint into {target}...")
        r = run(cmd)
        if r.returncode != 0:
            log.warn(f"pip install failed: {(r.stderr or r.stdout).strip()}")

        if has("leanblueprint"):
            log.success(f"leanblueprint: {version(['leanblueprint', '--version'])}")
            if not venv:
                ensure_path_in_rc()
            return True

        log.error("leanblueprint not found after install.")
        log.step("Install manually: pip install -U leanblueprint")
        log.step("See: https://github.com/PatrickMassot/leanblueprint")
        return False
