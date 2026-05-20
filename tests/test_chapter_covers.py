"""Tests for `% archon:covers` chapter→files coverage.

A consolidated blueprint chapter can declare the multiple Lean files it
blueprints; the prover-dispatch gate then maps those files to that
chapter instead of the strict 1:1 slug, and the blueprint doctor lints
the declaration for integrity (covered file exists; no double coverage).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archon.commands.tooling.blueprint import (
    chapter_coverage_map,
    chapter_slug_for_lean_file,
    lean_file_to_chapter_slug,
    parse_chapter_covers,
)
from archon.commands.loop.blueprint_doctor import _scan_covers_problems


class ParseCoversTest(unittest.TestCase):
    def test_whitespace_and_comma_and_multiline_and_dedup(self):
        text = (
            "\\chapter{Rigidity}\n"
            "% archon:covers A.lean Sub/B.lean\n"
            "% archon:covers Sub/C.lean, A.lean\n"
            "\\label{ch:R}\n"
        )
        self.assertEqual(
            parse_chapter_covers(text),
            ["A.lean", "Sub/B.lean", "Sub/C.lean"],
        )

    def test_no_declaration_is_empty(self):
        self.assertEqual(parse_chapter_covers("\\chapter{X}\n"), [])


class CoverageMapTest(unittest.TestCase):
    def _project(self) -> Path:
        d = tempfile.mkdtemp()
        p = Path(d)
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        ch = p / "blueprint" / "src" / "chapters"
        ch.mkdir(parents=True)
        (ch / "Big.tex").write_text(
            "\\chapter{Big}\n% archon:covers Big.lean Sub/Thin.lean\n",
            encoding="utf-8",
        )
        (ch / "Plain.tex").write_text("\\chapter{Plain}\n", encoding="utf-8")
        (p / "Big.lean").write_text("-- x", encoding="utf-8")
        (p / "Sub").mkdir()
        (p / "Sub" / "Thin.lean").write_text("-- y", encoding="utf-8")
        return p

    def test_only_declaring_chapters_appear(self):
        p = self._project()
        self.assertEqual(
            chapter_coverage_map(p), {"Big": ["Big.lean", "Sub/Thin.lean"]}
        )

    def test_covers_wins_over_slug(self):
        p = self._project()
        # Sub/Thin.lean's 1:1 slug would be Sub_Thin, but Big covers it.
        self.assertEqual(chapter_slug_for_lean_file(p, "Sub/Thin.lean"), "Big")

    def test_uncovered_file_falls_back_to_slug(self):
        p = self._project()
        self.assertEqual(
            chapter_slug_for_lean_file(p, "Other/File.lean"),
            lean_file_to_chapter_slug("Other/File.lean"),
        )


class CoversIntegrityTest(unittest.TestCase):
    def test_missing_file_and_double_coverage_flagged(self):
        d = tempfile.mkdtemp()
        p = Path(d)
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        ch = p / "blueprint" / "src" / "chapters"
        ch.mkdir(parents=True)
        (ch / "Big.tex").write_text(
            "\\chapter{Big}\n% archon:covers Real.lean Gone.lean\n", encoding="utf-8"
        )
        (ch / "Two.tex").write_text(
            "\\chapter{Two}\n% archon:covers Real.lean\n", encoding="utf-8"
        )
        (p / "Real.lean").write_text("-- x", encoding="utf-8")

        kinds = sorted(k for k, _ in _scan_covers_problems(p))
        self.assertIn("missing_file", kinds)      # Gone.lean
        self.assertIn("double_coverage", kinds)    # Real.lean in Big + Two

    def test_clean_coverage_has_no_problems(self):
        d = tempfile.mkdtemp()
        p = Path(d)
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        ch = p / "blueprint" / "src" / "chapters"
        ch.mkdir(parents=True)
        (ch / "Big.tex").write_text(
            "\\chapter{Big}\n% archon:covers Real.lean\n", encoding="utf-8"
        )
        (p / "Real.lean").write_text("-- x", encoding="utf-8")
        self.assertEqual(_scan_covers_problems(p), [])


if __name__ == "__main__":
    unittest.main()
