"""Install (or upgrade) the `leanblueprint` Python CLI."""

from __future__ import annotations

import sys

from archon import log

from ..shell import ensure_path_in_rc, has, run, version
from .base import DependencyCheck


class LeanBlueprintCheck(DependencyCheck):
    name = "leanblueprint"

    def run(self) -> bool:
        already = has("leanblueprint")
        action = "Upgrading" if already else "Installing"
        log.step(f"{action} leanblueprint via pip --user...")
        r = run([
            sys.executable, "-m", "pip", "install", "--user", "--upgrade", "leanblueprint",
        ])
        if r.returncode != 0:
            log.warn(f"pip install failed: {(r.stderr or r.stdout).strip()}")

        if has("leanblueprint"):
            log.success(f"leanblueprint: {version(['leanblueprint', '--version'])}")
            ensure_path_in_rc()
            return True

        log.error("leanblueprint not found after install.")
        log.step("Install manually: pip install -U leanblueprint")
        log.step("See: https://github.com/PatrickMassot/leanblueprint")
        return False
