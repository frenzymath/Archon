"""Tests for the STRATEGY.md soft-bound check in plan-validate.

The plan-prompt's canonical STRATEGY.md schema caps the file at ~250
lines / ~12 KB working, ~400 / ~20 KB hard-warn. The validator does
not gate the iter on this — it stamps ``planValidate.strategyBloat``
in meta.json and logs a warning — but it MUST detect:

* large files (line count > 400),
* large files (byte count > 20 KB),
* inline ```lean fences (a "no Lean dumps" violation),
* inline LaTeX ``\\begin{theorem|lemma|proof|definition}`` envs.

When none of those conditions hold, the validator is silent.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from archon.commands.loop.plan_validate import _check_strategy_bounds


class _FakeCtx:
    """Minimal stand-in for LoopContext — only the fields the bound
    check actually reads."""

    def __init__(self, state_dir: Path, iter_meta: Path | None) -> None:
        self.state_dir = state_dir
        self.iter_meta = iter_meta


class CheckStrategyBoundsTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.state = self.root / ".archon"
        self.state.mkdir()
        self.strategy = self.state / "STRATEGY.md"
        self.meta = self.state / "meta.json"
        self.meta.write_text("{}", encoding="utf-8")
        self.ctx = _FakeCtx(self.state, self.meta)

    def tearDown(self):
        self._td.cleanup()

    def _meta(self) -> dict:
        return json.loads(self.meta.read_text(encoding="utf-8"))

    def test_silent_when_strategy_missing(self):
        # No file at all → no warning, no meta stamp.
        with mock.patch("archon.commands.loop.plan_validate.log") as mlog:
            _check_strategy_bounds(self.ctx)
            mlog.warn.assert_not_called()
        self.assertEqual(self._meta(), {})

    def test_silent_when_within_bounds(self):
        self.strategy.write_text(
            "# Strategy\n## Goal\nFormalize X.\n## Phases\n",
            encoding="utf-8",
        )
        with mock.patch("archon.commands.loop.plan_validate.log") as mlog:
            _check_strategy_bounds(self.ctx)
            mlog.warn.assert_not_called()
        self.assertEqual(self._meta(), {})

    def test_warns_when_too_many_lines(self):
        # 401 lines — just past the hard ceiling.
        body = "# Strategy\n" + "stuff\n" * 401
        self.strategy.write_text(body, encoding="utf-8")
        with mock.patch("archon.commands.loop.plan_validate.log") as mlog:
            _check_strategy_bounds(self.ctx)
            mlog.warn.assert_called_once()
            msg = mlog.warn.call_args[0][0]
        self.assertIn("STRATEGY.md bloat", msg)
        self.assertIn("lines", msg)
        meta = self._meta()
        self.assertTrue(meta["planValidate"]["strategyBloat"])
        self.assertGreater(meta["planValidate"]["strategyLines"], 400)

    def test_warns_on_inline_lean_fence(self):
        body = (
            "# Strategy\n## Goal\nFormalize X.\n\n"
            "```lean\ntheorem foo : True := by trivial\n```\n"
        )
        self.strategy.write_text(body, encoding="utf-8")
        with mock.patch("archon.commands.loop.plan_validate.log") as mlog:
            _check_strategy_bounds(self.ctx)
            mlog.warn.assert_called_once()
            msg = mlog.warn.call_args[0][0]
        self.assertIn("lean fence", msg)
        meta = self._meta()
        self.assertEqual(meta["planValidate"]["strategyLeanFences"], 1)

    def test_warns_on_inline_tex_env(self):
        body = (
            "# Strategy\n## Goal\nFormalize X.\n\n"
            "\\begin{theorem}\\label{thm:x}A.\\end{theorem}\n"
        )
        self.strategy.write_text(body, encoding="utf-8")
        with mock.patch("archon.commands.loop.plan_validate.log") as mlog:
            _check_strategy_bounds(self.ctx)
            mlog.warn.assert_called_once()
        meta = self._meta()
        self.assertEqual(meta["planValidate"]["strategyTexEnvs"], 1)

    def test_silent_when_meta_is_none(self):
        # The validator should not crash when meta isn't writable; it
        # still logs but skips the meta stamp.
        self.strategy.write_text("# Strategy\n" + "x\n" * 500, encoding="utf-8")
        ctx = _FakeCtx(self.state, iter_meta=None)
        with mock.patch("archon.commands.loop.plan_validate.log") as mlog:
            _check_strategy_bounds(ctx)
            mlog.warn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
