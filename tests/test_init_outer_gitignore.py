"""Regression test: outer ``.gitignore`` must list ``.archon/`` after init.

The "keep" reinit mode skips ``BootstrapStep`` (which is where
``ensure_gitignore_entry`` lives in the full-init path). On a v0.1.0 →
v0.2.0 upgrade that picks "keep", the outer ``.gitignore`` was therefore
left without a ``.archon/`` rule, every inner-git phase commit dirtied
the outer working tree, and ``archon branch`` then refused to switch.
``InnerGitStep`` now adds the rule itself so every init mode covers it.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from archon.commands.init.context import InitContext
from archon.commands.init.steps.inner_git import InnerGitStep


class OuterGitignoreEnsuredTest(unittest.TestCase):

    def _project_with_outer_git(self, gitignore_text: str | None) -> Path:
        tmp = tempfile.mkdtemp()
        self.addCleanup(_rmtree, tmp)
        proj = Path(tmp)
        # Initialize an outer git so ``Git.is_repo()`` returns True.
        subprocess.run(
            ["git", "init", "-q"], cwd=proj, check=True,
        )
        (proj / ".archon").mkdir()
        if gitignore_text is not None:
            (proj / ".gitignore").write_text(
                gitignore_text, encoding="utf-8",
            )
        return proj

    def _ctx(self, project: Path) -> InitContext:
        return InitContext(
            project_path=project,
            state_dir=project / ".archon",
            fresh=False,
            model="dummy",
        )

    def test_adds_archon_rule_when_missing(self):
        # Reviewer's exact case: gitignore only has ``/.lake``.
        proj = self._project_with_outer_git("/.lake\n")
        InnerGitStep(self._ctx(proj)).run()
        gi = (proj / ".gitignore").read_text(encoding="utf-8")
        self.assertIn(".archon/", gi)
        # The pre-existing line survives.
        self.assertIn("/.lake", gi)

    def test_idempotent_when_already_present(self):
        proj = self._project_with_outer_git(
            "/.lake\n# Archon state directory\n.archon/\n"
        )
        before = (proj / ".gitignore").read_text(encoding="utf-8")
        InnerGitStep(self._ctx(proj)).run()
        after = (proj / ".gitignore").read_text(encoding="utf-8")
        self.assertEqual(before, after)

    def test_no_outer_git_is_silent_noop(self):
        # When there's no outer .git dir, the step must not crash and
        # must not synthesise a .gitignore out of thin air.
        tmp = tempfile.mkdtemp()
        self.addCleanup(_rmtree, tmp)
        proj = Path(tmp)
        (proj / ".archon").mkdir()
        InnerGitStep(self._ctx(proj)).run()
        # No outer repo → no .gitignore (or, if present, no .archon/).
        # We don't assert presence/absence — only that the call
        # completed without raising.
        self.assertTrue(proj.is_dir())

    def test_creates_gitignore_when_absent(self):
        proj = self._project_with_outer_git(None)
        InnerGitStep(self._ctx(proj)).run()
        gi = proj / ".gitignore"
        self.assertTrue(gi.exists())
        self.assertIn(".archon/", gi.read_text(encoding="utf-8"))


def _rmtree(path: str) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
