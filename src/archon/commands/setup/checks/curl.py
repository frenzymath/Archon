"""Probe for `curl` (required for several no-sudo bootstrap installers)."""

from __future__ import annotations

from archon import log

from ..shell import has
from .base import DependencyCheck


class CurlCheck(DependencyCheck):
    name = "curl"

    def run(self) -> bool:
        if has("curl"):
            log.success("curl: available")
            return True

        log.step("curl not found, attempting install...")
        self.installer.install("curl")

        if has("curl"):
            log.success("curl: installed")
            return True
        log.error("curl is required and is not available.")
        return False
