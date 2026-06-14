"""Tests for loop sorry-count helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archon.commands.loop import sorry_count



class FileOpenSorryCountFallbackTest(unittest.TestCase):
    def test_fallback_counts_sorryax_and_admit(self):
        with tempfile.TemporaryDirectory() as d:
            lean = Path(d) / "T.lean"
            lean.write_text(
                "theorem a : True := by sorryAx\n"
                "theorem b : True := by admit\n"
                "-- sorryAx in a comment is ignored\n"
                "def nonsorryAxName := 1\n",
                encoding="utf-8",
            )

            original = sorry_count.data_path
            sorry_count.data_path = lambda _rel: Path(d) / "missing_analyzer.py"
            try:
                self.assertEqual(sorry_count.file_open_sorry_count(lean), 2)
            finally:
                sorry_count.data_path = original


if __name__ == "__main__":
    unittest.main()
