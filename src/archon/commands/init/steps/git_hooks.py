"""Install the secret-detection git hooks into outer + inner git.

Runs after :class:`InnerGitStep` so the inner-git's ``hooks/`` directory
already exists. Two hooks are installed:

- ``pre-commit`` auto-scrubs staged files in place, replacing every
  API-key-shaped string with ``ARCHON_REDACTED_SECRET`` so the commit
  proceeds with clean content (primary defense).
- ``pre-push`` is a final-check abort — if a secret-shaped string ever
  reaches a commit (e.g. bypassed pre-commit), the push is refused with
  a clear message rather than silently leaking.

Touches the outer ``<project>/.git/hooks/`` only when the user already
has an outer git repo. Never overwrites a non-Archon hook of the same
name — surfaces a warning instead so the user can merge the logic by
hand.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from archon import log

from ..utils import data_path
from .base import InitStep


class GitHooksStep(InitStep):
    """Install Archon's pre-commit scrub + pre-push secret-scan hooks."""

    name = "Secret-detection git hooks"
    number = 9

    # (hook_name, marker substrings used to recognize Archon-managed
    # copies for safe refresh-in-place)
    _HOOKS: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("pre-commit", (
            "Archon pre-commit secret-scrub hook",
        )),
        ("pre-push", (
            "Archon pre-push secret-detection hook",
            "Pre-push hook: refuse to push commits that introduce an API-key-shaped",
        )),
    )

    def run(self) -> None:
        log.phase(self.number, self.name)

        installed = 0
        for hook_name, markers in self._HOOKS:
            hook_src = data_path(f"git-hooks/{hook_name}")
            if not hook_src.exists():
                log.warn(f"Hook template missing: {hook_src} — skipping")
                continue

            for label, hooks_dir in self._target_hook_dirs():
                if not hooks_dir.parent.is_dir():
                    # The git-dir itself doesn't exist (no outer git repo,
                    # for example) — skip silently.
                    continue
                hooks_dir.mkdir(parents=True, exist_ok=True)
                dst = hooks_dir / hook_name
                if dst.exists():
                    if self._is_archon_hook(dst, markers):
                        shutil.copy2(hook_src, dst)
                        dst.chmod(0o755)
                        log.step(f"Refreshed {label} {hook_name} hook (Archon-managed)")
                        installed += 1
                    else:
                        log.warn(
                            f"{label} {hook_name} hook already exists and looks "
                            f"custom — not overwriting. Merge Archon's secret "
                            f"scrub manually or move yours aside and re-run."
                        )
                    continue
                shutil.copy2(hook_src, dst)
                dst.chmod(0o755)
                log.step(f"Installed {label} {hook_name} hook at {dst}")
                installed += 1

        if installed == 0:
            log.info("No git hooks installed (no writable hooks/ dir found)")
        else:
            log.success(
                f"Secret-detection active: {installed} hook(s) installed. "
                f"pre-commit auto-scrubs API-key-shaped strings to "
                f"'ARCHON_REDACTED_SECRET'; pre-push blocks anything that "
                f"slipped through."
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
    def _is_archon_hook(path: Path, markers: tuple[str, ...]) -> bool:
        """True iff the existing file is one of Archon's bundled hooks
        (safe to refresh in place). Each hook has its own set of marker
        substrings that should never appear in a hand-written hook.
        """
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:4096]
        except OSError:
            return False
        return any(m in head for m in markers)
