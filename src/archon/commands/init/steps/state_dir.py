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

        claude_src = template_dir / "CLAUDE.md"
        claude_dst = ctx.state_dir / "CLAUDE.md"
        if claude_src.exists():
            # Only warn when the file is actually about to change. The
            # previous warning fired even on a clean re-init where the
            # user hadn't edited CLAUDE.md, which was misleading.
            if (
                claude_dst.exists()
                and not ctx.fresh
                and not _files_equal(claude_src, claude_dst)
            ):
                log.warn("CLAUDE.md will be overwritten with the latest bundled version.")
            copy_file(claude_src, claude_dst, overwrite=True)
        else:
            log.warn("Template not found: CLAUDE.md")

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
