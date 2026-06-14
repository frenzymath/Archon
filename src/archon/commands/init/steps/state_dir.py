"""Create `.archon/` directory tree and copy template files."""

from __future__ import annotations

from archon import log

from ..utils import _files_equal, copy_file, data_path, fail_permission
from .base import InitStep


_USER_STATE_FILES = (
    "PROGRESS.md",
    "STRATEGY.md",
    "USER_HINTS.md",
    "ARCHON_MEMORY.md",
    "task_pending.md",
    "task_done.md",
)
# Note: REFACTOR_DIRECTIVE.md is no longer created on init. The
# autonomous loop uses the refactor *subagent* with inline directives,
# not a file. The interactive `archon refactor draft` command still
# writes the file on demand when the user runs it by hand.

_SUBDIRS = (
    "task_results", "logs", "prompts",
    "proof-journal/sessions", "proof-journal/current_session",
)


class StateDirStep(InitStep):
    name = "Setting up .archon/ state directory"
    number = 1

    def run(self) -> None:
        ctx = self.ctx
        log.phase(self.number, self.name)

        for subdir in _SUBDIRS:
            try:
                (ctx.state_dir / subdir).mkdir(parents=True, exist_ok=True)
            except PermissionError as e:
                fail_permission(ctx.state_dir / subdir, e)
        log.step("Created directory tree")

        template_dir = data_path("archon-template")
        copied = 0
        preserved = 0

        for name in _USER_STATE_FILES:
            src = template_dir / name
            dst = ctx.state_dir / name
            if not src.exists():
                log.warn(f"Template not found: {name}")
                continue
            if dst.exists():
                preserved += 1
                continue
            copy_file(src, dst)
            copied += 1

        # AGENTS.md — the bundled agent role doc (was CLAUDE.md pre-0.3.0).
        # AGENTS.md is the cross-tool convention auto-loaded by both Claude
        # Code and Codex; Archon also references it explicitly in every prompt.
        agents_src = template_dir / "AGENTS.md"
        agents_dst = ctx.state_dir / "AGENTS.md"
        legacy_dst = ctx.state_dir / "CLAUDE.md"
        if agents_src.exists():
            # Only warn when the file is actually about to change. The
            # previous warning fired even on a clean re-init where the
            # user hadn't edited the role doc, which was misleading.
            if (
                agents_dst.exists()
                and not ctx.fresh
                and not _files_equal(agents_src, agents_dst)
            ):
                log.warn("AGENTS.md will be overwritten with the latest bundled version.")
            copy_file(agents_src, agents_dst, overwrite=True)
            # Migrate older projects: drop the now-unused .archon/CLAUDE.md so
            # it can't drift from AGENTS.md. (The role doc is bundled, not
            # user-edited, so nothing custom is lost.)
            if legacy_dst.exists():
                try:
                    legacy_dst.unlink()
                    log.step("Migrated .archon/CLAUDE.md → AGENTS.md")
                except OSError:
                    pass
        else:
            log.warn("Template not found: AGENTS.md")

        # MULTILANE.md — reference doc for setting up additional providers.
        # Always overwrite with the bundled version: it's a reference, not a
        # user-edited file.
        multilane_doc_src = template_dir / "MULTILANE.md"
        if multilane_doc_src.exists():
            copy_file(multilane_doc_src, ctx.state_dir / "MULTILANE.md", overwrite=True)

        log.step(
            f"Copied {copied} new template file(s)"
            + (f", preserved {preserved} existing" if not ctx.fresh else "")
        )
        log.success("State directory ready")
