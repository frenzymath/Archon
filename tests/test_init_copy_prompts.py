"""Regression tests for ``CopyPromptsStep`` on legacy symlink layouts.

v0.1.0 projects placed ``.archon/prompts/*.md`` as symlinks back into
the installed package's bundled ``prompts/`` directory. The v0.2.0 init
flow copies real files instead. Without an explicit ``unlink`` before
``shutil.copy2``, the symlink target equals the source path and Python
raises ``SameFileError`` — observed during a v0.1.0 → v0.2.0 upgrade
via ``archon init --force`` (or "overwrite" in the interactive menu).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from archon.commands.init.context import InitContext
from archon.commands.init.steps.copy_prompts import CopyPromptsStep
from archon.commands.init.utils import data_path


class CopyPromptsSymlinkRegressionTest(unittest.TestCase):
    """``CopyPromptsStep`` must handle legacy symlinks without crashing."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(_rmtree, self._tmp)
        self.project = Path(self._tmp)
        (self.project / ".archon" / "prompts").mkdir(parents=True)
        # Replicate the v0.1.0 layout: every bundled prompt is a symlink
        # back at the package data.
        self.prompts_src = data_path("prompts")
        for f in sorted(self.prompts_src.glob("*.md")):
            link = self.project / ".archon" / "prompts" / f.name
            link.symlink_to(f)

    def _ctx(self, *, fresh: bool) -> InitContext:
        return InitContext(
            project_path=self.project,
            state_dir=self.project / ".archon",
            fresh=fresh,
            model="dummy",
        )

    def test_fresh_path_replaces_legacy_symlinks(self):
        # The previous fresh path called ``shutil.copy2`` on a symlink
        # whose target equalled the source — SameFileError.
        CopyPromptsStep(self._ctx(fresh=True)).run()
        for f in sorted(self.prompts_src.glob("*.md")):
            dst = self.project / ".archon" / "prompts" / f.name
            self.assertFalse(
                dst.is_symlink(),
                f"{dst} should be a real file after the fresh init path",
            )
            self.assertEqual(dst.read_bytes(), f.read_bytes())

    def test_non_fresh_path_replaces_legacy_symlinks(self):
        CopyPromptsStep(self._ctx(fresh=False)).run()
        for f in sorted(self.prompts_src.glob("*.md")):
            dst = self.project / ".archon" / "prompts" / f.name
            self.assertFalse(dst.is_symlink())
            self.assertEqual(dst.read_bytes(), f.read_bytes())

    def test_non_fresh_path_preserves_user_edited_file(self):
        # A real file with user edits must survive a non-fresh re-init.
        for f in sorted(self.prompts_src.glob("*.md"))[:1]:
            dst = self.project / ".archon" / "prompts" / f.name
            dst.unlink()  # remove the symlink first
            dst.write_text("USER EDITS DO NOT TOUCH\n", encoding="utf-8")
        CopyPromptsStep(self._ctx(fresh=False)).run()
        for f in sorted(self.prompts_src.glob("*.md"))[:1]:
            dst = self.project / ".archon" / "prompts" / f.name
            self.assertEqual(
                dst.read_text(encoding="utf-8"),
                "USER EDITS DO NOT TOUCH\n",
                "non-fresh path must preserve a user-edited real file",
            )


class InitCommandFreshFlagTest(unittest.TestCase):
    """``InitCommand`` must demote ``ctx.fresh`` to False for any reinit
    mode (overwrite / merge / keep). The bug was that overwrite kept
    ctx.fresh = True, sending CopyPromptsStep down the symlink-blind
    path.
    """

    def _patches(self, mode: str):
        """Patch the heavy bits so we exercise just the ctx.fresh flow."""
        from archon.commands.init import command as cmd_mod

        # Pretend Claude is installed.
        p_has = patch.object(cmd_mod, "has", return_value=True)
        # Skip the entire step pipeline; we only care about ctx.fresh
        # after _resolve_reinit_mode runs.
        p_keep = patch.object(cmd_mod.InitCommand, "_run_keep_only")
        p_full = patch.object(cmd_mod.InitCommand, "_run_full_init")
        # Force the mode the test wants.
        p_mode = patch.object(
            cmd_mod.InitCommand, "_resolve_reinit_mode", return_value=mode,
        )
        # Prompt merger is heavy; stub.
        p_merger = patch.object(cmd_mod, "PromptMerger")
        # Version warn does I/O.
        p_warn = patch.object(cmd_mod, "warn_if_mismatch")
        return p_has, p_keep, p_full, p_mode, p_merger, p_warn

    def _run_init(self, mode: str):
        from archon.commands.init.command import InitCommand
        tmp = tempfile.mkdtemp()
        self.addCleanup(_rmtree, tmp)
        proj = Path(tmp)
        cmd = InitCommand(str(proj), force=False)
        patches = self._patches(mode)
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        cmd.run()
        return cmd.ctx

    def test_overwrite_mode_sets_fresh_false(self):
        ctx = self._run_init("overwrite")
        self.assertFalse(ctx.fresh)

    def test_merge_mode_sets_fresh_false(self):
        ctx = self._run_init("merge")
        self.assertFalse(ctx.fresh)

    def test_keep_mode_sets_fresh_false(self):
        ctx = self._run_init("keep")
        self.assertFalse(ctx.fresh)

    def test_fresh_mode_stays_fresh_true(self):
        ctx = self._run_init("fresh")
        self.assertTrue(ctx.fresh)


class CopyPromptsVariantsTest(unittest.TestCase):
    """``CopyPromptsStep`` must also copy the ``variants/*.md`` subdir.

    The codex prompt variant ships at
    ``.archon-src/prompts/variants/codex.md``; an init (fresh or merge)
    must land it at ``.archon/prompts/variants/codex.md`` so an
    existing-project re-init picks it up and the runner's local-override
    resolution has something to find / let the user edit.
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(_rmtree, self._tmp)
        self.project = Path(self._tmp)
        (self.project / ".archon" / "prompts").mkdir(parents=True)
        self.prompts_src = data_path("prompts")

    def _ctx(self, *, fresh: bool) -> InitContext:
        return InitContext(
            project_path=self.project,
            state_dir=self.project / ".archon",
            fresh=fresh,
            model="dummy",
        )

    def test_fresh_copies_variant_files(self):
        CopyPromptsStep(self._ctx(fresh=True)).run()
        for f in sorted(self.prompts_src.glob("variants/*.md")):
            dst = self.project / ".archon" / "prompts" / "variants" / f.name
            self.assertTrue(dst.is_file(), f"{dst} should exist after fresh init")
            self.assertEqual(dst.read_bytes(), f.read_bytes())

    def test_merge_adds_new_variant_to_existing_project(self):
        # Simulate an existing project that has top-level prompts but no
        # variants dir yet (pre-variants layout). A non-fresh re-init must
        # add the variant without touching anything else.
        CopyPromptsStep(self._ctx(fresh=False)).run()
        dst = self.project / ".archon" / "prompts" / "variants" / "codex.md"
        self.assertTrue(dst.is_file())
        self.assertNotIn("\x00", dst.read_text(encoding="utf-8"))

    def test_merge_preserves_user_edited_variant(self):
        vdir = self.project / ".archon" / "prompts" / "variants"
        vdir.mkdir(parents=True)
        (vdir / "codex.md").write_text("USER EDITED VARIANT\n", encoding="utf-8")
        CopyPromptsStep(self._ctx(fresh=False)).run()
        self.assertEqual(
            (vdir / "codex.md").read_text(encoding="utf-8"),
            "USER EDITED VARIANT\n",
        )


def _rmtree(path: str) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
