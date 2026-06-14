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

        from .harness_select import resolve_harness_selection

        ctx = self.ctx
        log.phase(self.number, self.name)

        env_written = env_loader.write_env_template(ctx.project_path)
        if env_written:
            log.step("Wrote .archon/.env (alternative-provider keys, gitignored)")
        else:
            log.step(".archon/.env already exists — preserved")

        config_exists = project_config.config_path(ctx.project_path).exists()
        selection = None if config_exists else resolve_harness_selection(ctx)

        cfg_written = project_config.write_default_config(
            ctx.project_path, harness_selection=selection,
        )
        if cfg_written:
            log.step(
                "Wrote .archon/config.json with default loop + multilane "
                f"settings (harness: {_describe_selection(selection)})"
            )
        else:
            cfg_migrated = project_config.migrate_project_config(ctx.project_path)
            if cfg_migrated:
                log.step(
                    ".archon/config.json updated — new keys added "
                    "(your existing values were preserved)"
                )
            else:
                log.step(".archon/config.json already up to date — preserved")
        log.success("Project config ready")


def _describe_selection(selection: object) -> str:
    """One-line human summary of the resolved harness selection for logs."""
    if selection is None:
        return "claude-code (default)"
    if isinstance(selection, str):
        return f"{selection} (all roles)"
    if isinstance(selection, dict):
        if not selection:
            return "claude-code (default)"
        roles = ", ".join(f"{r}={n}" for r, n in selection.items())
        return f"mixed ({roles}; others claude-code)"
    return str(selection)
