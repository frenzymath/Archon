"""Tests for the loop's auto-capture + auto-clear of USER_HINTS.md.

The plan-phase helpers must:

* read the file at plan-start (capture) — returning ``None`` when the
  file is missing rather than raising;
* reset the file to the bundled template after the plan phase succeeds
  (so the init state and the cleared state match — a clean cycle that
  doesn't pollute the next planner's "user hints" view with stale
  content from the iter we just consumed);
* retain temporary hints added or rewritten after the plan-start capture,
  so they reach the following plan phase rather than being lost on clear;
* leave the file alone when the captured content was empty.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archon.commands.loop.phases.plan import (
    _capture_user_hints,
    _clear_user_hints,
    _read_user_hints_template,
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
    """After clear, USER_HINTS.md content must equal the bundled template.

    The bundled template ships empty so the init state and the cleared
    state match — but the contract is "matches the template", not
    "is empty". Tests assert the contract so a future template tweak
    (a brief discoverability comment, say) propagates without breaking
    the test suite.
    """

    def test_clears_non_empty_file_to_template(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            hints = state / "USER_HINTS.md"
            hints.write_text("- some hint", encoding="utf-8")
            _clear_user_hints(state)
            self.assertEqual(
                hints.read_text(encoding="utf-8"),
                _read_user_hints_template(),
            )

    def test_idempotent_when_already_at_template(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            hints = state / "USER_HINTS.md"
            hints.write_text(_read_user_hints_template(), encoding="utf-8")
            _clear_user_hints(state)
            self.assertEqual(
                hints.read_text(encoding="utf-8"),
                _read_user_hints_template(),
            )

    def test_preserves_temporary_hint_added_after_capture(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            hints = state / "USER_HINTS.md"
            template = _read_user_hints_template()
            consumed = "- [2026-01-01T00:00:00Z] consumed by this plan\n"
            added = "- [2026-01-01T00:01:00Z] added while plan is running\n"
            captured = template.replace(
                "## Temporary hints\n\n",
                "## Temporary hints\n\n" + consumed,
                1,
            )
            hints.write_text(captured, encoding="utf-8")

            # Simulate a user writing a new one-shot instruction while the
            # plan agent is working from the earlier snapshot.
            hints.write_text(captured.replace(consumed, consumed + added), encoding="utf-8")
            _clear_user_hints(state, captured)

            self.assertEqual(
                hints.read_text(encoding="utf-8"),
                template.replace(
                    "## Temporary hints\n\n",
                    "## Temporary hints\n\n" + added,
                    1,
                ),
            )

    def test_preserves_new_temporary_hint_when_capture_had_only_persistent(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            hints = state / "USER_HINTS.md"
            template = _read_user_hints_template()
            persistent = "- [2026-01-01T00:00:00Z] standing directive\n"
            added = "- [2026-01-01T00:01:00Z] added while plan is running\n"
            captured = template.replace(
                "## Persistent hints\n\n",
                "## Persistent hints\n\n" + persistent,
                1,
            )
            current = captured.replace(
                "## Temporary hints\n\n",
                "## Temporary hints\n\n" + added,
                1,
            )
            hints.write_text(current, encoding="utf-8")

            _clear_user_hints(state, captured)

            self.assertEqual(
                hints.read_text(encoding="utf-8"),
                current,
            )

    def test_preserves_temporary_hint_rewritten_after_capture(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            hints = state / "USER_HINTS.md"
            template = _read_user_hints_template()
            original = "- [2026-01-01T00:00:00Z] original wording\n"
            rewritten = "- [2026-01-01T00:00:00Z] rewritten wording\n"
            captured = template.replace(
                "## Temporary hints\n\n",
                "## Temporary hints\n\n" + original,
                1,
            )
            hints.write_text(captured.replace(original, rewritten), encoding="utf-8")
            _clear_user_hints(state, captured)

            self.assertEqual(
                hints.read_text(encoding="utf-8"),
                template.replace(
                    "## Temporary hints\n\n",
                    "## Temporary hints\n\n" + rewritten,
                    1,
                ),
            )

    def test_creates_file_when_missing(self):
        # Clear creates the file if it didn't exist — the post-condition
        # is "file matches template", regardless of prior state.
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            _clear_user_hints(state)
            hints = state / "USER_HINTS.md"
            self.assertTrue(hints.is_file())
            self.assertEqual(
                hints.read_text(encoding="utf-8"),
                _read_user_hints_template(),
            )

    def test_template_ships_with_html_comment_preamble(self):
        # The template ships with an HTML-comment preamble explaining
        # the hint format to the user. The section headings (## Temporary
        # hints / ## Persistent hints) sit outside the comment so the user
        # can see them when editing the file directly. _user_hints_block
        # recognises that both section bodies are empty and renders the
        # template as "no hints" — the planner never sees the headings as
        # hint content. The preamble is the format guide; if you remove it,
        # also update _user_hints_block's comment-stripping contract.
        from archon.prompts import _user_hints_block, _strip_html_comments  # type: ignore
        import re
        tmpl = _read_user_hints_template()
        self.assertTrue(tmpl.lstrip().startswith("<!--"))
        self.assertIn("USER_HINTS.md", tmpl)
        # Template-only content must render as "no hints" to the planner.
        self.assertIn("No user hints", _user_hints_block(tmpl))
        # No actual hint bullets outside the HTML comment.
        outside_comment = _strip_html_comments(tmpl)
        bullet_re = re.compile(r"^- \[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\]", re.MULTILINE)
        self.assertFalse(bullet_re.search(outside_comment))


class TemplateConsistencyTest(unittest.TestCase):
    """The init state and the post-clear state must match.

    Init copies the bundled template into ``.archon/USER_HINTS.md`` on
    fresh projects; the loop clears the same file back to the template
    after each plan consumes hints. Drift between the two would mean
    the very first plan-phase capture sees different content than every
    subsequent one — a confusing inconsistency.
    """

    def test_init_state_matches_cleared_state(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d)
            # Simulate init: copy the template into the state dir.
            template_content = _read_user_hints_template()
            (state / "USER_HINTS.md").write_text(
                template_content, encoding="utf-8",
            )
            init_state = (state / "USER_HINTS.md").read_text(encoding="utf-8")
            # Simulate iter cycle: user adds a TEMPORARY hint under the
            # correct heading, planner consumes it, loop clears.
            # Place the hint under "## Temporary hints" (not at EOF, which
            # would land it in the persistent section and correctly preserve it).
            hint_line = "- [2026-01-01T00:00:00Z] some live hint\n"
            file_with_hint = template_content.replace(
                "## Temporary hints\n\n",
                "## Temporary hints\n\n" + hint_line,
                1,
            )
            (state / "USER_HINTS.md").write_text(file_with_hint, encoding="utf-8")
            _clear_user_hints(state)
            cleared_state = (state / "USER_HINTS.md").read_text(encoding="utf-8")
            self.assertEqual(init_state, cleared_state)


if __name__ == "__main__":
    unittest.main()
