"""Regression test: git hooks are stamped with the running interpreter.

The bundled hooks ship with ``#!/usr/bin/env python3``. uv-managed
virtualenvs may only expose ``python`` (no ``python3`` alias), so the
hook then aborts with ``env: python3: No such file or directory``.
``GitHooksStep`` now rewrites the shebang to the interpreter
``archon`` itself is running under, pinning the hook to a known-good
Python.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from archon.commands.init.context import InitContext
from archon.commands.init.steps.git_hooks import GitHooksStep


class HookShebangStampTest(unittest.TestCase):

    def _project_with_outer_git(self) -> Path:
        tmp = tempfile.mkdtemp()
        self.addCleanup(_rmtree, tmp)
        proj = Path(tmp)
        subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
        (proj / ".archon" / "git-dir" / "hooks").mkdir(parents=True)
        return proj

    def _ctx(self, project: Path) -> InitContext:
        return InitContext(
            project_path=project,
            state_dir=project / ".archon",
            fresh=True,
            model="dummy",
        )

    def test_outer_hook_shebang_is_current_interpreter(self):
        proj = self._project_with_outer_git()
        GitHooksStep(self._ctx(proj)).run()
        for name in ("pre-commit", "pre-push"):
            hook = proj / ".git" / "hooks" / name
            self.assertTrue(hook.exists(), f"{name} not installed")
            first = hook.read_text(encoding="utf-8").splitlines()[0]
            self.assertEqual(first, f"#!{sys.executable}")

    def test_inner_hook_shebang_is_current_interpreter(self):
        proj = self._project_with_outer_git()
        GitHooksStep(self._ctx(proj)).run()
        for name in ("pre-commit", "pre-push"):
            hook = proj / ".archon" / "git-dir" / "hooks" / name
            self.assertTrue(hook.exists(), f"{name} not installed")
            first = hook.read_text(encoding="utf-8").splitlines()[0]
            self.assertEqual(first, f"#!{sys.executable}")

    def test_hook_body_preserved_after_shebang_stamp(self):
        # The marker comment Archon uses to recognise its own hooks must
        # still be present after the rewrite — otherwise re-running
        # `archon init` would mistake the hook for a custom one.
        proj = self._project_with_outer_git()
        GitHooksStep(self._ctx(proj)).run()
        hook = proj / ".git" / "hooks" / "pre-commit"
        text = hook.read_text(encoding="utf-8")
        self.assertIn("Archon pre-commit secret-scrub hook", text)

    def test_installed_hook_is_executable(self):
        proj = self._project_with_outer_git()
        GitHooksStep(self._ctx(proj)).run()
        hook = proj / ".git" / "hooks" / "pre-commit"
        mode = hook.stat().st_mode & 0o777
        # User exec bit must be set so git actually invokes the hook.
        self.assertTrue(mode & 0o100, f"hook not executable (mode={oct(mode)})")


def _rmtree(path: str) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
