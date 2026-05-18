"""Tests for the loop's auto-capture + auto-clear of USER_HINTS.md.

The plan-phase helpers must:

* read the file at plan-start (capture) — returning ``None`` when the
  file is missing rather than raising;
* clear the file with an empty write after the plan phase succeeds;
* leave the file alone when the captured content was empty (so a
  pre-existing empty file isn't pointlessly rewritten).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archon.commands.loop.phases.plan import (
    _capture_user_hints,
    _clear_user_hints,
)


class CaptureUserHintsTest(unittest.TestCase):
    def test_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(_capture_user_hints(Path(d)))

    def test_empty_file_returns_empty_string(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            (state / "USER_HINTS.md").write_text("", encoding="utf-8")
            got = _capture_user_hints(state)
            self.assertEqual(got, "")

    def test_returns_full_content_including_whitespace(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            text = "  \n- [ts] hint with leading whitespace\n\n"
            (state / "USER_HINTS.md").write_text(text, encoding="utf-8")
            got = _capture_user_hints(state)
            self.assertEqual(got, text)


class ClearUserHintsTest(unittest.TestCase):
    def test_clears_non_empty_file(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            hints = state / "USER_HINTS.md"
            hints.write_text("- some hint", encoding="utf-8")
            _clear_user_hints(state)
            self.assertEqual(hints.read_text(encoding="utf-8"), "")

    def test_idempotent_on_already_empty(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            hints = state / "USER_HINTS.md"
            hints.write_text("", encoding="utf-8")
            _clear_user_hints(state)
            self.assertEqual(hints.read_text(encoding="utf-8"), "")

    def test_creates_file_when_missing(self):
        # _clear writes "", so it creates the file if it didn't exist.
        # That's fine: a freshly empty file is the post-condition the
        # planner expects regardless of prior state.
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            _clear_user_hints(state)
            hints = state / "USER_HINTS.md"
            self.assertTrue(hints.is_file())
            self.assertEqual(hints.read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
