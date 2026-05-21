"""Verify Python >= 3.10 is in use (no install — it's the host runtime)."""

from __future__ import annotations

import sys

from archon import log

from .base import DependencyCheck


class PythonCheck(DependencyCheck):
    name = "Python"

    def run(self) -> bool:
        v = sys.version_info
        if v >= (3, 10):
            log.success(f"Python: {v.major}.{v.minor}.{v.micro}")
            return True
        log.error(f"Python 3.10+ required, found {v.major}.{v.minor}.{v.micro}")
        log.step("Install: https://www.python.org/downloads/")
        return False
