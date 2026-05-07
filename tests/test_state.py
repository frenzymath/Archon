"""Regression tests for archon.state stage parsing.

Run from the repo root with either::

    PYTHONPATH=src python -m unittest tests.test_state

or, after an editable install (``pip install -e .``)::

    python -m unittest tests.test_state
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from textwrap import dedent

from archon.state import is_complete, read_stage


def _write_progress(tmp: Path, stage_line: str) -> Path:
    """Write a minimal PROGRESS.md whose ``## Current Stage`` value is *stage_line*."""
    progress = tmp / "PROGRESS.md"
    progress.write_text(
        dedent(
            f"""\
            # Project Progress

            ## Current Stage
            {stage_line}

            ## Stages
            - [x] init
            """
        )
    )
    return progress


class ReadStageTests(unittest.TestCase):
    def test_bare_stage_token(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = _write_progress(Path(td), "polish")
            self.assertEqual(read_stage(p), "polish")

    def test_stage_with_parenthetical_annotation(self) -> None:
        # The plan agent may annotate the stage line with extra context.
        # read_stage must return only the canonical token so downstream
        # equality checks (is_complete, "init" guard, prover-{stage}.md
        # path resolution) keep working.
        with tempfile.TemporaryDirectory() as td:
            p = _write_progress(
                Path(td),
                "COMPLETE  (re-attained 2026-05-07 via polish iter-001)",
            )
            self.assertEqual(read_stage(p), "COMPLETE")

    def test_force_stage_overrides_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = _write_progress(Path(td), "polish")
            self.assertEqual(read_stage(p, force_stage="prover"), "prover")

    def test_missing_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileNotFoundError):
                read_stage(Path(td) / "nope.md")

    def test_no_current_stage_section_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "PROGRESS.md"
            p.write_text("# Project Progress\n\nno header here\n")
            with self.assertRaises(ValueError):
                read_stage(p)


class IsCompleteTests(unittest.TestCase):
    def test_complete_token(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = _write_progress(Path(td), "COMPLETE")
            self.assertTrue(is_complete(p))

    def test_complete_with_annotation(self) -> None:
        # The original bug: the loop kept spinning plan + review for many
        # iters after COMPLETE because is_complete did an exact equality
        # check against the full annotated line.
        with tempfile.TemporaryDirectory() as td:
            p = _write_progress(
                Path(td),
                "COMPLETE  (re-attained 2026-05-07 via polish iter-001)",
            )
            self.assertTrue(is_complete(p))

    def test_not_complete(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = _write_progress(Path(td), "polish")
            self.assertFalse(is_complete(p))

    def test_missing_file_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(is_complete(Path(td) / "nope.md"))


if __name__ == "__main__":
    unittest.main()
