"""Copy bundled subagent files into `.claude/agents/`."""

from __future__ import annotations

from archon import log

from ..utils import copy_file, data_path
from .base import InitStep


class AgentsStep(InitStep):
    name = "Installing subagent files"
    number = 6  # after SkillsStep (5)

    def run(self) -> None:
        ctx = self.ctx
        log.phase(self.number, self.name)

        agents_src = data_path("agents")
        agents_dst = ctx.project_path / ".claude" / "agents"
        agents_dst.mkdir(parents=True, exist_ok=True)

        if not agents_src.exists():
            log.error(f"Agents directory not found at {agents_src}")
            return

        new = 0
        preserved = 0
        for f in sorted(agents_src.glob("*.md")):
            dst = agents_dst / f.name
            if ctx.fresh:
                copy_file(f, dst, overwrite=True)
                new += 1
                continue

            if dst.exists():
                if dst.is_symlink():
                    # Replace any legacy symlink with a real copy.
                    dst.unlink()
                    copy_file(f, dst, overwrite=True)
                    new += 1
                else:
                    preserved += 1
                continue

            copy_file(f, dst)
            new += 1

        if ctx.fresh:
            log.success(f"Copied {new} subagent file(s)")
        else:
            log.success(
                f"Added {new} new subagent file(s), preserved {preserved} existing"
            )