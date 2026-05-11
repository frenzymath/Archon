"""Install the pre-push secret-detection hook into outer + inner git.

Runs after :class:`InnerGitStep` so the inner-git's ``hooks/`` directory
already exists. Touches the outer ``<project>/.git/hooks/`` only when
the user already has an outer git repo. Never overwrites an existing
``pre-push`` — if one is present, we leave it alone and surface a
warning so the user can merge the secret-scan logic by hand.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from archon import log

from ..utils import data_path
from .base import InitStep


class GitHooksStep(InitStep):
    """Install Archon's pre-push secret-scan hook."""

    name = "Secret-detection git hooks"
    number = 9

    def run(self) -> None:
        ctx = self.ctx
        log.phase(self.number, self.name)

        hook_src = data_path("git-hooks/pre-push")
        if not hook_src.exists():
            log.warn(f"Hook template missing: {hook_src} — skipping")
            return

        installed = 0
        for label, hooks_dir in self._target_hook_dirs():
            if not hooks_dir.parent.is_dir():
                # The git-dir itself doesn't exist (no outer git repo,
                # for example) — skip silently.
                continue
            hooks_dir.mkdir(parents=True, exist_ok=True)
            dst = hooks_dir / "pre-push"
            if dst.exists():
                if self._is_archon_hook(dst):
                    # Refresh to the latest bundled version.
                    shutil.copy2(hook_src, dst)
                    dst.chmod(0o755)
                    log.step(f"Refreshed {label} pre-push hook (Archon-managed)")
                    installed += 1
                else:
                    log.warn(
                        f"{label} pre-push hook already exists and looks "
                        f"custom — not overwriting. Add Archon's secret "
                        f"scan manually or move yours aside and re-run."
                    )
                continue
            shutil.copy2(hook_src, dst)
            dst.chmod(0o755)
            log.step(f"Installed {label} pre-push hook at {dst}")
            installed += 1

        if installed == 0:
            log.info("No git hooks installed (no writable hooks/ dir found)")
        else:
            log.success(
                f"Pre-push secret-detection active on {installed} git "
                f"director(y/ies) — pushes carrying API-key-shaped strings "
                f"will be blocked."
            )

    # ── helpers ───────────────────────────────────────────────────────

    def _target_hook_dirs(self) -> list[tuple[str, Path]]:
        """(label, hooks_dir) pairs for the gits we want to protect.

        Both can be missing in practice: the outer git may not have been
        initialized, and on a degraded init the inner git-dir may also be
        absent. The caller filters those out.
        """
        ctx = self.ctx
        outer_git = ctx.project_path / ".git"
        inner_git = ctx.state_dir / "git-dir"
        return [
            ("outer git (.git)",                outer_git / "hooks"),
            ("inner git (.archon/git-dir)",     inner_git / "hooks"),
        ]

    @staticmethod
    def _is_archon_hook(path: Path) -> bool:
        """True iff the existing file is Archon's bundled hook (safe to
        refresh in place). Detected by a marker string in our template
        that's unlikely to appear in any other pre-push hook.
        """
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:4096]
        except OSError:
            return False
        return "Archon pre-push secret-detection hook" in head or \
               "Pre-push hook: refuse to push commits that introduce an API-key-shaped" in head
