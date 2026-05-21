"""Tests for ``_blueprint_chapter_hint`` chapter-path resolution.

The prover prompt embeds a "Blueprint chapter for your file: <path>" line.
The path is derived from the Lean source path; chapters that depart from
the underscore-prefix convention (e.g. ``AbelianVarietyRigidity.tex`` for
``AlgebraicJacobian/AbelianVarietyRigidity.lean``) used to be unreachable
because the prompt only spelt the full-slug path. A basename fallback
keeps such chapters discoverable.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archon.prompts import _blueprint_chapter_hint


class BlueprintChapterHintTest(unittest.TestCase):

    def _make_project(self) -> Path:
        tmp = tempfile.mkdtemp()
        self.addCleanup(_rmtree, tmp)
        root = Path(tmp)
        (root / "blueprint" / "src" / "chapters").mkdir(parents=True)
        return root

    def test_no_blueprint_returns_empty(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(_rmtree, str(root))
        self.assertEqual(_blueprint_chapter_hint(root, "Foo/Bar.lean"), "")

    def test_full_slug_chapter_wins_when_present(self):
        root = self._make_project()
        (root / "blueprint" / "src" / "chapters" / "Foo_Bar.tex").write_text("x")
        hint = _blueprint_chapter_hint(root, "Foo/Bar.lean")
        self.assertIn("blueprint/src/chapters/Foo_Bar.tex", hint)

    def test_basename_fallback_when_slug_missing(self):
        # Regression: the prover prompt used to point at the full-slug
        # path even when only the basename-named chapter existed on disk.
        root = self._make_project()
        (root / "blueprint" / "src" / "chapters" / "AbelianVarietyRigidity.tex"
        ).write_text("x")
        hint = _blueprint_chapter_hint(
            root, "AlgebraicJacobian/AbelianVarietyRigidity.lean",
        )
        self.assertIn(
            "blueprint/src/chapters/AbelianVarietyRigidity.tex", hint,
        )
        self.assertNotIn(
            "AlgebraicJacobian_AbelianVarietyRigidity.tex", hint,
        )

    def test_both_present_prefers_full_slug(self):
        # If both the full-slug and basename chapter exist, the full-slug
        # one is the canonical target — don't silently switch.
        root = self._make_project()
        chapters = root / "blueprint" / "src" / "chapters"
        (chapters / "AlgebraicJacobian_AbelianVarietyRigidity.tex").write_text("x")
        (chapters / "AbelianVarietyRigidity.tex").write_text("x")
        hint = _blueprint_chapter_hint(
            root, "AlgebraicJacobian/AbelianVarietyRigidity.lean",
        )
        self.assertIn(
            "AlgebraicJacobian_AbelianVarietyRigidity.tex", hint,
        )

    def test_missing_chapter_falls_back_to_full_slug_path(self):
        # When neither file exists, the hint still spells the full-slug
        # path so the prover knows where to create the new chapter.
        root = self._make_project()
        hint = _blueprint_chapter_hint(
            root, "AlgebraicJacobian/AbelianVarietyRigidity.lean",
        )
        self.assertIn(
            "AlgebraicJacobian_AbelianVarietyRigidity.tex", hint,
        )

    def test_archon_covers_still_resolves(self):
        # An explicit ``% archon:covers`` declaration on a consolidated
        # chapter must continue to win — the basename fallback only
        # applies AFTER the covers-aware slug fails to find a file.
        root = self._make_project()
        chapters = root / "blueprint" / "src" / "chapters"
        (chapters / "Consolidated.tex").write_text(
            "% archon:covers Foo/Bar.lean\n",
        )
        hint = _blueprint_chapter_hint(root, "Foo/Bar.lean")
        self.assertIn("blueprint/src/chapters/Consolidated.tex", hint)


def _rmtree(path: str) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
