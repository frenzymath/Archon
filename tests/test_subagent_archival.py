"""Tests for the subagent CLI's auto-archival step.

After ``archon subagent <name>`` runs, the result report is at
``task_results/<name>-<slug>.md`` (root) or
``task_results/<parent>/<name>-<slug>.md`` (nested). The CLI also
copies it to ``logs/iter-NNN/<name>-<slug>-report.md`` (or, when
nested, ``logs/iter-NNN/<parent>/<name>-<slug>-report.md``) so the
dashboard can render it within the same iter — the dispatching plan
agent does NOT need to `cp` the file.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archon.commands.subagent import _archive_subagent_report
from archon.subagents.base import ROOT_PARENT_SLUG


class ArchiveSubagentReportTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.iter_log = self.root / ".archon" / "logs" / "iter-007"
        self.iter_log.mkdir(parents=True)

    def tearDown(self):
        self._td.cleanup()

    def _make_report(self, *parts: str, body: str = "# report\n") -> Path:
        p = self.root / ".archon" / "task_results" / Path(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        return p

    def test_archives_root_report(self):
        report = self._make_report("blueprint-writer-x42.md", body="# X42")
        archived = _archive_subagent_report(
            report, self.iter_log, ROOT_PARENT_SLUG,
            "blueprint-writer", "x42",
        )
        expected = self.iter_log / "blueprint-writer-x42-report.md"
        self.assertEqual(archived, expected)
        self.assertEqual(archived.read_text(encoding="utf-8"), "# X42")
        # Source still exists — the archive is a COPY, not a move.
        self.assertTrue(report.is_file())

    def test_archives_nested_report_under_parent_dir(self):
        report = self._make_report(
            "parent-slug", "reference-retriever-r1.md", body="# R1",
        )
        archived = _archive_subagent_report(
            report, self.iter_log, "parent-slug",
            "reference-retriever", "r1",
        )
        expected = (
            self.iter_log / "parent-slug" / "reference-retriever-r1-report.md"
        )
        self.assertEqual(archived, expected)
        self.assertEqual(archived.read_text(encoding="utf-8"), "# R1")

    def test_missing_report_returns_none(self):
        nowhere = self.root / "no-such-report.md"
        archived = _archive_subagent_report(
            nowhere, self.iter_log, ROOT_PARENT_SLUG, "foo", "bar",
        )
        self.assertIsNone(archived)

    def test_none_path_returns_none(self):
        archived = _archive_subagent_report(
            None, self.iter_log, ROOT_PARENT_SLUG, "foo", "bar",  # type: ignore[arg-type]
        )
        self.assertIsNone(archived)


if __name__ == "__main__":
    unittest.main()
