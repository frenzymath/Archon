"""Tests that the bundled sorry_analyzer skips `.archon/` and `.git/`
directories.

Without this exclusion, the analyzer descends into Archon's per-step
Lean snapshots under `.archon/logs/iter-NNN/snapshots/` and counts
every snapshot's sorries, inflating the project total by orders of
magnitude (e.g. 3826 vs 11 on a real project).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def _analyzer_path() -> Path:
    return Path(__file__).resolve().parent.parent / (
        "src/archon/.archon-src/skills/lean4/lib/scripts/sorry_analyzer.py"
    )


class AnalyzerExclusionsTest(unittest.TestCase):
    def _run(self, target: Path) -> int:
        r = subprocess.run(
            [sys.executable, str(_analyzer_path()), str(target),
             "--format=summary", "--exit-zero-on-findings"],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(r.returncode, 0, f"analyzer failed: {r.stderr}")
        first = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ""
        # Expected format: "Sorry Summary: N total across M file(s)"
        import re
        m = re.search(r"Sorry Summary:\s*(\d+)\s+total", first)
        self.assertIsNotNone(m, f"unexpected first line: {first!r}")
        return int(m.group(1))

    def test_archon_dir_is_excluded(self):
        """Sorries under `<project>/.archon/` must not be counted."""
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            # Real source file: 2 sorries.
            (proj / "src").mkdir()
            (proj / "src" / "Foo.lean").write_text(
                "theorem foo : True := by sorry\n"
                "theorem bar : 1 = 1 := by sorry\n",
                encoding="utf-8",
            )
            # Archon snapshots: each carries 5 sorries; without
            # exclusion these would dominate the count.
            snapshots = proj / ".archon" / "logs" / "iter-001" / "snapshots" / "Foo"
            snapshots.mkdir(parents=True)
            snapshot_content = "\n".join(
                f"theorem step_{i} : True := by sorry" for i in range(5)
            )
            for step in ("baseline", "step-001", "step-002", "step-003"):
                (snapshots / f"{step}.lean").write_text(
                    snapshot_content + "\n", encoding="utf-8",
                )
            count = self._run(proj)
            self.assertEqual(
                count, 2,
                "expected only the 2 real-source sorries, "
                f"got {count} (analyzer didn't exclude .archon/)",
            )

    def test_git_dir_is_excluded(self):
        """`.git/` should be pruned even though it rarely contains .lean."""
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            (proj / "src").mkdir()
            (proj / "src" / "Foo.lean").write_text(
                "theorem foo : True := by sorry\n", encoding="utf-8",
            )
            git_dir = proj / ".git" / "stuff"
            git_dir.mkdir(parents=True)
            (git_dir / "Pathological.lean").write_text(
                "\n".join(f"theorem x{i} : True := by sorry" for i in range(99)),
                encoding="utf-8",
            )
            count = self._run(proj)
            self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
