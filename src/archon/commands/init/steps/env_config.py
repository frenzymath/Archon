"""Create `.archon/.env` and `.archon/config.json` with sensible defaults.

Both files are idempotent: existing user content is left alone. The
`.env` is gitignored by both outer (.archon/ rule) and inner git
(default-excludes rule, see InnerGit). config.json is tracked by inner
git so it's versioned with the project.
"""

from __future__ import annotations

from archon import log

from .base import InitStep


class EnvAndConfigStep(InitStep):
    name = "Project config (.archon/config.json) and .env"
    number = 9

    def run(self) -> None:
        from archon.commands.tooling import env_loader, project_config

        ctx = self.ctx
        log.phase(self.number, self.name)

        env_written = env_loader.write_env_template(ctx.project_path)
        if env_written:
            log.step("Wrote .archon/.env (alternative-provider keys, gitignored)")
        else:
            log.step(".archon/.env already exists — preserved")

        cfg_written = project_config.write_default_config(ctx.project_path)
        if cfg_written:
            log.step("Wrote .archon/config.json with default loop + multilane settings")
        else:
            log.step(".archon/config.json already exists — preserved")
        log.success("Project config ready")
