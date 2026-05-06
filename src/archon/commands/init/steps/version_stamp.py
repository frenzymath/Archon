"""Stamp the current CLI version into `.archon/VERSION`."""

from __future__ import annotations

from archon import log
from archon.commands.tooling.version import ProjectVersion

from .base import InitStep


class VersionStampStep(InitStep):
    name = "Version stamp"
    # Logically step 10, but the original code reused log.phase(9) for both
    # this and EnvAndConfigStep — kept that way to preserve console output.
    number = 9

    def run(self) -> None:
        ctx = self.ctx
        log.phase(self.number, self.name)
        pv = ProjectVersion(ctx.project_path)
        previous = pv.read()
        written = pv.write()
        if previous is None:
            log.success(f"Stamped project version: {written}")
        elif previous != written:
            log.success(f"Updated project version: {previous} → {written}")
        else:
            log.step(f"Project version unchanged: {written}")
