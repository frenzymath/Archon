"""Initialize the inner `.archon/.git` repo and capture its baseline commit."""

from __future__ import annotations

from archon import log
from archon.commands.tooling.git import Git
from archon.commands.tooling.inner_git import InnerGit

from .base import InitStep


class InnerGitStep(InitStep):
    """Archon keeps its own versioning here — every agent phase commits.

    The outer (mathematician's) git repo is untouched after init. The
    initial commit captures the state at init time so subsequent phase
    commits have a baseline to diff against.
    """

    name = "Inner git (.archon/.git)"
    number = 8

    def run(self) -> None:
        ctx = self.ctx
        log.phase(self.number, self.name)

        # ``BootstrapStep`` already does this on the full-init path, but
        # the "keep" reinit mode skips bootstrap entirely. A v0.1.0 → v0.2.0
        # upgrade that picks "keep" then leaves the outer ``.gitignore``
        # without a ``.archon/`` rule, so every phase commit dirties the
        # outer working tree (and ``archon branch`` then refuses to switch).
        # Ensuring it here makes the rule land in every init mode.
        outer = Git(ctx.project_path, auto_init=False)
        if outer.is_repo():
            if outer.ensure_gitignore_entry(
                ".archon/", comment="Archon state directory",
            ):
                log.step("Added '.archon/' to outer .gitignore")

        if not InnerGit.available():
            log.warn("`git` not on PATH — skipping inner-git setup. Run: archon setup")
            return

        inner = InnerGit(ctx.project_path)
        created = inner.init()
        if created:
            log.step("Initialized inner git at .archon/git-dir")
        else:
            log.step("Inner git already present at .archon/git-dir")

        # Refresh info/exclude on every init so existing projects pick
        # up new exclusion patterns (notably .env). `init()` only writes
        # the file on first creation, so without this older repos would
        # keep their stale rules forever.
        if inner.ensure_excludes():
            log.step("Refreshed inner-git info/exclude with latest patterns")

        made_initial = inner.ensure_initial_commit("archon[000/init]: initial state")
        if made_initial:
            log.success(f"Inner git first commit: {inner.head_sha() or '?'}")
        else:
            log.info(f"Inner git HEAD: {inner.head_sha() or '?'}")
