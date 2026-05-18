"""Tests for the plan-validate hook.

Covers:

* The intentional no-prover skip marker is recognized inside
  ``## Current Objectives`` and treated as a legitimate state.
* Without the marker (and no parseable objectives), the validator
  appends a discuss-format hint and returns False.
* The hint format is single-line, discuss-compatible.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from archon.commands.loop.plan_validate import (
    _append_hint,
    _has_intentional_skip_marker,
    _INTENTIONAL_SKIP_RE,
)


class IntentionalSkipMarkerTest(unittest.TestCase):
    def _write_progress(self, body: str) -> Path:
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        p = Path(self.td.name) / "PROGRESS.md"
        p.write_text(body, encoding="utf-8")
        return p

    def test_marker_recognized(self):
        p = self._write_progress(
            "# Progress\n\n"
            "## Current Objectives\n\n"
            "(no prover dispatch this iter — see iter/iter-116/plan.md for rationale)\n"
        )
        self.assertTrue(_has_intentional_skip_marker(p))

    def test_marker_recognized_with_extra_prose(self):
        p = self._write_progress(
            "## Current Objectives\n\n"
            "The planner has paused this iter pending user response.\n"
            "(no prover dispatch this iter)\n"
            "Resume scheduled iter-117+.\n"
        )
        self.assertTrue(_has_intentional_skip_marker(p))

    def test_marker_only_in_other_sections_not_recognized(self):
        # Marker outside `## Current Objectives` does not count.
        p = self._write_progress(
            "## Past iters\n(no prover dispatch this iter happened before)\n\n"
            "## Current Objectives\n\n"
            "### 1. **`Foo.lean`**\n"
        )
        self.assertFalse(_has_intentional_skip_marker(p))

    def test_no_marker_returns_false(self):
        p = self._write_progress(
            "## Current Objectives\n\n"
            "### 1. **`Foo.lean`**\n"
        )
        self.assertFalse(_has_intentional_skip_marker(p))

    def test_missing_section_returns_false(self):
        p = self._write_progress("# Progress\n\n(no other content)\n")
        self.assertFalse(_has_intentional_skip_marker(p))

    def test_missing_file_returns_false(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "does-not-exist.md"
            self.assertFalse(_has_intentional_skip_marker(p))


class IntentionalSkipRegexTest(unittest.TestCase):
    def test_regex_variants_match(self):
        ok = [
            "(no prover dispatch this iter)",
            "(no prover this iter)",
            "(No Prover Dispatch This Iter — context here)",
            "see iter/...: (no prover dispatch this iteration)",
        ]
        for s in ok:
            self.assertIsNotNone(
                _INTENTIONAL_SKIP_RE.search(s),
                f"expected marker match in: {s!r}",
            )

    def test_regex_unrelated_text_no_match(self):
        nope = [
            "the prover dispatched this iter",
            "no prover lane scheduled (later)",   # parens but wrong content
            "this iter ran 4 provers",
        ]
        for s in nope:
            self.assertIsNone(
                _INTENTIONAL_SKIP_RE.search(s),
                f"unexpected marker match in: {s!r}",
            )


class AppendHintFormatTest(unittest.TestCase):
    def test_hint_is_discuss_format_single_line(self):
        with tempfile.TemporaryDirectory() as d:
            hints = Path(d) / "USER_HINTS.md"
            _append_hint(hints)
            body = hints.read_text(encoding="utf-8")
            # Should contain a single discuss-format line `- [ts] ...`.
            lines = [
                l for l in body.splitlines()
                if l.strip().startswith("- [")
            ]
            self.assertEqual(len(lines), 1, f"expected one bullet line, got: {body!r}")
            # The line carries an ISO-8601 UTC timestamp in square brackets.
            self.assertRegex(
                lines[0],
                r"- \[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\]",
            )
            # And carries the archon[plan-validate] tag for discovery.
            self.assertIn("archon[plan-validate]", lines[0])

    def test_hint_appends_to_existing_content(self):
        with tempfile.TemporaryDirectory() as d:
            hints = Path(d) / "USER_HINTS.md"
            hints.write_text("# User Hints\n\n", encoding="utf-8")
            _append_hint(hints)
            body = hints.read_text(encoding="utf-8")
            self.assertTrue(body.startswith("# User Hints"))
            self.assertIn("archon[plan-validate]", body)


if __name__ == "__main__":
    unittest.main()
