"""Regression tests for ``sync_leanok._remove_leanok``.

The old line regex (``^\\s*\\leanok\\b[^\\n]*\\n?``) swallowed *everything*
after ``\\leanok`` on the same line. When prose authors wrote
``\\leanok \\(x = y\\)`` the whole line — including the opening ``\\(`` — was
deleted, orphaning the closing ``\\)`` later in the block and crashing
plastex / leanblueprint web with a RecursionError. ``_remove_leanok`` must
only take the macro itself, never the surrounding math/prose.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


_SYNC = Path(__file__).resolve().parent.parent / (
    "src/archon/.archon-src/skills/lean4/lib/scripts/sync_leanok.py"
)


def _load_sync_module():
    name = "sync_leanok_module"
    spec = importlib.util.spec_from_file_location(name, _SYNC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod


class RemoveLeanokTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_sync_module()

    def test_standalone_line_is_removed(self):
        body = "  \\label{foo}\n  \\leanok\n  Some prose.\n"
        out = self.mod._remove_leanok(body)
        self.assertNotIn("\\leanok", out)
        self.assertIn("\\label{foo}", out)
        self.assertIn("Some prose.", out)

    def test_inline_math_delimiters_are_preserved(self):
        # The bug: this whole line used to be deleted, orphaning the \).
        body = "\\leanok \\(x = y\\)\nrest of proof with closing \\)\n"
        out = self.mod._remove_leanok(body)
        self.assertNotIn("\\leanok", out)
        # The math that shared the line must survive.
        self.assertIn("\\(x = y\\)", out)
        # Delimiters stay balanced — no orphaned \( or \).
        self.assertEqual(out.count("\\("), 1)
        self.assertEqual(out.count("\\)"), 2)

    def test_trailing_prose_after_macro_is_preserved(self):
        body = "\\leanok by the argument above.\n"
        out = self.mod._remove_leanok(body)
        self.assertNotIn("\\leanok", out)
        self.assertIn("by the argument above.", out)

    def test_noop_when_no_marker(self):
        body = "\\label{foo}\nplain prose, no marker\n"
        self.assertEqual(self.mod._remove_leanok(body), body)


if __name__ == "__main__":
    unittest.main()
