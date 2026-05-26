"""Tests for the overwrite-mode fixes introduced in v0.3.0.

Covers:
- ``ctx.overwrite`` flag: CopyPromptsStep force-replaces changed files
  but skips identical ones (no spurious I/O or misleading counts).
- Extra-file warnings: Phase 2 warns about prompt files present locally
  but absent from the bundled set.
- ``copy_file`` return value: True when the file was actually written,
  False when content was already identical (silent no-op).
- ``ReinitController._diff_summary``: returns both ``changed`` and
  ``extras`` lists so the pre-menu inventory is complete.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from archon.commands.init.context import InitContext
from archon.commands.init.steps.copy_prompts import CopyPromptsStep
from archon.commands.init.utils import _files_equal, copy_file, data_path
from archon.commands.init.reinit import ReinitController


# ── helpers ───────────────────────────────────────────────────────────


def _rmtree(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _ctx(project: Path, *, fresh: bool = False, overwrite: bool = False) -> InitContext:
    return InitContext(
        project_path=project,
        state_dir=project / ".archon",
        fresh=fresh,
        overwrite=overwrite,
        model="dummy",
    )


# ── copy_file return value ────────────────────────────────────────────


class CopyFileReturnValueTest(unittest.TestCase):
    """``copy_file`` must return True iff the file was actually written."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(_rmtree, self._tmp)
        self.root = Path(self._tmp)
        self.src = self.root / "src.txt"
        self.src.write_bytes(b"content")

    def test_returns_true_when_destination_missing(self):
        dst = self.root / "new.txt"
        result = copy_file(self.src, dst)
        self.assertTrue(result)
        self.assertTrue(dst.exists())

    def test_returns_true_when_content_differs(self):
        dst = self.root / "dst.txt"
        dst.write_bytes(b"different")
        result = copy_file(self.src, dst, overwrite=True)
        self.assertTrue(result)

    def test_returns_false_when_content_identical(self):
        dst = self.root / "dst.txt"
        dst.write_bytes(b"content")
        result = copy_file(self.src, dst, overwrite=True)
        self.assertFalse(result)

    def test_returns_false_and_no_write_on_identical(self):
        dst = self.root / "dst.txt"
        dst.write_bytes(b"content")
        mtime_before = dst.stat().st_mtime_ns
        copy_file(self.src, dst, overwrite=True)
        self.assertEqual(dst.stat().st_mtime_ns, mtime_before)


# ── CopyPromptsStep with ctx.overwrite ───────────────────────────────


class CopyPromptsOverwriteFlagTest(unittest.TestCase):
    """``ctx.overwrite=True`` replaces changed files and skips identical ones."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(_rmtree, self._tmp)
        self.project = Path(self._tmp)
        self.prompts_dst = self.project / ".archon" / "prompts"
        self.prompts_dst.mkdir(parents=True)
        self.prompts_src = data_path("prompts")

    def _seed_prompts(self, *, modified: list[str] | None = None) -> None:
        """Copy bundled prompts into the project, optionally modifying some."""
        for f in sorted(self.prompts_src.glob("*.md")):
            dst = self.prompts_dst / f.name
            shutil.copy2(f, dst)
        for name in (modified or []):
            (self.prompts_dst / name).write_text("USER EDITS\n", encoding="utf-8")

    def test_overwrite_replaces_modified_file(self):
        self._seed_prompts(modified=["plan.md"])
        CopyPromptsStep(_ctx(self.project, overwrite=True)).run()
        bundled = (self.prompts_src / "plan.md").read_bytes()
        self.assertEqual((self.prompts_dst / "plan.md").read_bytes(), bundled)

    def test_overwrite_does_not_rewrite_identical_files(self):
        """Identical files must not be rewritten — mtime stays unchanged."""
        self._seed_prompts()
        mtimes_before = {
            f.name: f.stat().st_mtime_ns
            for f in self.prompts_dst.glob("*.md")
        }
        CopyPromptsStep(_ctx(self.project, overwrite=True)).run()
        for name, before in mtimes_before.items():
            after = (self.prompts_dst / name).stat().st_mtime_ns
            self.assertEqual(
                after, before,
                f"{name} was rewritten even though content was identical",
            )

    def test_non_overwrite_preserves_user_edits(self):
        """``ctx.overwrite=False`` (merge/keep) must not touch existing files."""
        self._seed_prompts(modified=["plan.md"])
        CopyPromptsStep(_ctx(self.project, overwrite=False)).run()
        self.assertEqual(
            (self.prompts_dst / "plan.md").read_text(encoding="utf-8"),
            "USER EDITS\n",
        )

    def test_overwrite_flag_set_by_init_command_for_overwrite_mode(self):
        """``InitCommand`` must set ``ctx.overwrite=True`` when mode is 'overwrite'."""
        from archon.commands.init import command as cmd_mod

        with patch.object(cmd_mod, "has", return_value=True), \
             patch.object(cmd_mod.InitCommand, "_run_full_init"), \
             patch.object(cmd_mod.InitCommand, "_resolve_reinit_mode", return_value="overwrite"), \
             patch.object(cmd_mod, "PromptMerger"), \
             patch.object(cmd_mod, "warn_if_mismatch"):
            from archon.commands.init.command import InitCommand
            cmd = InitCommand(str(self.project), force=False)
            cmd.run()
            self.assertTrue(cmd.ctx.overwrite)

    def test_overwrite_flag_not_set_for_merge_mode(self):
        """Merge mode preserves local edits — ``ctx.overwrite`` must be False."""
        from archon.commands.init import command as cmd_mod

        with patch.object(cmd_mod, "has", return_value=True), \
             patch.object(cmd_mod.InitCommand, "_run_full_init"), \
             patch.object(cmd_mod.InitCommand, "_resolve_reinit_mode", return_value="merge"), \
             patch.object(cmd_mod, "PromptMerger"), \
             patch.object(cmd_mod, "warn_if_mismatch"):
            from archon.commands.init.command import InitCommand
            cmd = InitCommand(str(self.project), force=False)
            cmd.run()
            self.assertFalse(cmd.ctx.overwrite)

    def test_overwrite_flag_set_for_fresh_mode(self):
        """Fresh installs must also set ``ctx.overwrite=True``."""
        from archon.commands.init import command as cmd_mod

        with patch.object(cmd_mod, "has", return_value=True), \
             patch.object(cmd_mod.InitCommand, "_run_full_init"), \
             patch.object(cmd_mod.InitCommand, "_resolve_reinit_mode", return_value="fresh"), \
             patch.object(cmd_mod, "PromptMerger"), \
             patch.object(cmd_mod, "warn_if_mismatch"):
            from archon.commands.init.command import InitCommand
            cmd = InitCommand(str(self.project), force=False)
            cmd.run()
            self.assertTrue(cmd.ctx.overwrite)


# ── extra-file warnings in CopyPromptsStep ───────────────────────────


class CopyPromptsExtraFileWarningTest(unittest.TestCase):
    """Phase 2 warns about local prompt files not in the bundled set."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(_rmtree, self._tmp)
        self.project = Path(self._tmp)
        self.prompts_dst = self.project / ".archon" / "prompts"
        self.prompts_dst.mkdir(parents=True)
        self.prompts_src = data_path("prompts")
        # Seed with bundled prompts.
        for f in sorted(self.prompts_src.glob("*.md")):
            shutil.copy2(f, self.prompts_dst / f.name)

    def _run_and_capture_warnings(self, extra_names: list[str]) -> list[str]:
        for name in extra_names:
            (self.prompts_dst / name).write_text("extra\n", encoding="utf-8")
        warnings: list[str] = []
        import archon.log as archon_log
        with patch.object(archon_log, "warn", side_effect=lambda msg: warnings.append(msg)):
            CopyPromptsStep(_ctx(self.project, overwrite=False)).run()
        return warnings

    def test_warns_about_extra_prompt_file(self):
        warnings = self._run_and_capture_warnings(["analogy.md"])
        self.assertTrue(
            any("analogy.md" in w for w in warnings),
            f"expected warning about analogy.md, got: {warnings}",
        )

    def test_warns_about_multiple_extra_files(self):
        extras = ["analogy.md", "coordinator.md", "challenger.md"]
        warnings = self._run_and_capture_warnings(extras)
        for name in extras:
            self.assertTrue(
                any(name in w for w in warnings),
                f"expected warning about {name}, got: {warnings}",
            )

    def test_no_warning_for_bundled_files(self):
        warnings = self._run_and_capture_warnings([])
        bundled_names = {f.name for f in self.prompts_src.glob("*.md")}
        for w in warnings:
            for name in bundled_names:
                self.assertNotIn(
                    name, w,
                    f"bundled file {name} should not trigger a warning",
                )

    def test_warning_message_says_not_to_delete(self):
        warnings = self._run_and_capture_warnings(["old-prompt.md"])
        self.assertTrue(
            any("safe to delete" in w for w in warnings),
            "warning should mention that deletion is safe if unneeded",
        )


# ── ReinitController._diff_summary ───────────────────────────────────


class DiffSummaryTest(unittest.TestCase):
    """``_diff_summary`` returns (changed, extras) correctly."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(_rmtree, self._tmp)
        self.project = Path(self._tmp)
        self.state_dir = self.project / ".archon"
        self.prompts_dst = self.state_dir / "prompts"
        self.prompts_dst.mkdir(parents=True)
        self.prompts_src = data_path("prompts")

    def _seed(self, *, modified: list[str] | None = None, extras: list[str] | None = None):
        for f in sorted(self.prompts_src.glob("*.md")):
            shutil.copy2(f, self.prompts_dst / f.name)
        for name in (modified or []):
            (self.prompts_dst / name).write_text("MODIFIED\n", encoding="utf-8")
        for name in (extras or []):
            (self.prompts_dst / name).write_text("EXTRA\n", encoding="utf-8")

    def test_no_changes_returns_empty_lists(self):
        self._seed()
        changed, extras = ReinitController(self.state_dir)._diff_summary()
        self.assertEqual(changed, [])
        self.assertEqual(extras, [])

    def test_modified_file_appears_in_changed(self):
        self._seed(modified=["plan.md"])
        changed, extras = ReinitController(self.state_dir)._diff_summary()
        self.assertTrue(any("plan.md" in p for p in changed))
        self.assertEqual(extras, [])

    def test_extra_file_appears_in_extras(self):
        self._seed(extras=["analogy.md"])
        changed, extras = ReinitController(self.state_dir)._diff_summary()
        self.assertEqual(changed, [])
        self.assertTrue(any("analogy.md" in p for p in extras))

    def test_modified_and_extra_both_reported(self):
        self._seed(modified=["plan.md"], extras=["coordinator.md"])
        changed, extras = ReinitController(self.state_dir)._diff_summary()
        self.assertTrue(any("plan.md" in p for p in changed))
        self.assertTrue(any("coordinator.md" in p for p in extras))

    def test_extra_file_not_in_changed(self):
        self._seed(extras=["old.md"])
        changed, extras = ReinitController(self.state_dir)._diff_summary()
        self.assertFalse(any("old.md" in p for p in changed))

    def test_modified_file_not_in_extras(self):
        self._seed(modified=["plan.md"])
        changed, extras = ReinitController(self.state_dir)._diff_summary()
        self.assertFalse(any("plan.md" in p for p in extras))


if __name__ == "__main__":
    unittest.main()
