"""Tests for ``auto_fix_objectives`` heading rewrites.

The plan agent has been observed to write objectives under
non-canonical headings — ``## Strategy``, ``## Objectives``,
lower-case variants — and the prover dispatcher silently empties out
when the canonical ``## Current Objectives`` is missing.

The reviewer flagged a missing case: ``## Current Strategy`` (plausible
because the plan prompt already frames work in terms of ``STRATEGY.md``,
so the agent may compose a hybrid). These tests pin the canonical
rewrites including the new ``## Current Strategy`` mapping.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archon.state.progress import auto_fix_objectives


_LEAN_FILE = "Foo/Bar.lean"
_OBJECTIVES_BODY = (
    "### 1. **Foo/Bar.lean**\n"
    "  - Prove the main theorem.\n"
)


class AutoFixObjectivesTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(_rmtree, self._tmp)
        self.project = Path(self._tmp)
        # Create the referenced lean file so parse_objective_files
        # resolves it (it filters by existence).
        target = self.project / _LEAN_FILE
        target.parent.mkdir(parents=True)
        target.write_text("-- placeholder\n", encoding="utf-8")
        self.progress = self.project / "PROGRESS.md"

    def _run(self, heading: str) -> tuple[list[Path], list[str]]:
        self.progress.write_text(
            f"## Current Stage\n\nprover\n\n{heading}\n\n{_OBJECTIVES_BODY}",
            encoding="utf-8",
        )
        return auto_fix_objectives(self.progress, self.project)

    def test_rewrites_strategy_to_current_objectives(self):
        objectives, fixes = self._run("## Strategy")
        self.assertEqual(len(objectives), 1)
        self.assertEqual(len(fixes), 1)
        self.assertIn("renamed '## Strategy'", fixes[0])

    def test_rewrites_current_strategy_drift(self):
        # The new case the reviewer flagged: plan agents have been
        # observed to write ``## Current Strategy`` when blending the
        # STRATEGY.md framing with the objectives section.
        objectives, fixes = self._run("## Current Strategy")
        self.assertEqual(len(objectives), 1)
        self.assertEqual(len(fixes), 1)
        self.assertIn("renamed '## Current Strategy'", fixes[0])
        # Post-fix the file's heading is the canonical one.
        self.assertIn(
            "## Current Objectives",
            self.progress.read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            "## Current Strategy",
            self.progress.read_text(encoding="utf-8"),
        )

    def test_canonical_heading_short_circuits(self):
        objectives, fixes = self._run("## Current Objectives")
        # No rename needed.
        self.assertEqual(fixes, [])
        self.assertEqual(len(objectives), 1)


def _rmtree(path: str) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
