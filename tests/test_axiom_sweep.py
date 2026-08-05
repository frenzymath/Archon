"""Tests for the sorryAx-laundering axiom sweep.

The sweep parses `check_axioms_inline.sh`'s human output, classifies
`sorryAx` dependencies as launderings, writes a report, and feeds the
launderings into the next plan prompt as open sorries.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from archon.commands.loop.axiom_sweep import (
    AxiomFinding,
    AxiomSweepReport,
    _ANSI_RE,
    _FINDING_RE,
    _script_path,
    write_reports,
)
from archon.prompts import _axiom_sweep_findings_block


class FindingParseTest(unittest.TestCase):
    def test_parses_decls_and_strips_ansi(self):
        raw = (
            "\x1b[0;34mFile: \x1b[1;33mFoo.lean\x1b[0m\n"
            "  \x1b[0;31m⚠ Foo.bar uses non-standard axiom: sorryAx\x1b[0m\n"
            "  ⚠ Foo.baz uses non-standard axiom: myAxiom\n"
        )
        clean = _ANSI_RE.sub("", raw)
        found = [
            AxiomFinding(m.group("decl"), m.group("axiom"))
            for m in _FINDING_RE.finditer(clean)
        ]
        self.assertEqual([f.decl for f in found], ["Foo.bar", "Foo.baz"])
        self.assertTrue(found[0].is_sorry)
        self.assertFalse(found[1].is_sorry)


class ReportTest(unittest.TestCase):
    def test_only_sorry_findings_are_launderings(self):
        rep = AxiomSweepReport(
            findings=[AxiomFinding("A.b", "sorryAx"), AxiomFinding("A.c", "myAx")],
            ran=True,
        )
        self.assertEqual([f.decl for f in rep.sorry_launderings], ["A.b"])
        self.assertEqual([f.decl for f in rep.other_axioms], ["A.c"])
        self.assertTrue(rep.has_launderings)


class ScriptDiscoveryTest(unittest.TestCase):
    def test_project_scan_excludes_archon_snapshots(self):
        with tempfile.TemporaryDirectory() as d:
            project = Path(d)
            source = project / "Main.lean"
            source.write_text("namespace Main\n\ndef value := 1\n")

            snapshot = (
                project / ".archon" / "logs" / "iter-005" / "snapshots"
                / "Main" / "baseline.lean"
            )
            snapshot.parent.mkdir(parents=True)
            snapshot.write_text(
                "namespace Main\n\n"
                "def stale : Nat := this_identifier_does_not_exist\n"
            )

            fake_bin = project / "fake-bin"
            fake_bin.mkdir()
            calls = project / "lake-calls.txt"
            fake_lake = fake_bin / "lake"
            fake_lake.write_text(
                "#!/bin/sh\n"
                'printf "%s\\n" "$3" >> "$AXIOM_SWEEP_TEST_CALLS"\n'
                "exit 0\n"
            )
            fake_lake.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
            env["AXIOM_SWEEP_TEST_CALLS"] = str(calls)
            result = subprocess.run(
                ["bash", str(_script_path()), ".", "--report-only"],
                cwd=project,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            checked = calls.read_text().splitlines()
            self.assertEqual(checked, [str(source.resolve())])
            self.assertNotIn(str(snapshot.resolve()), checked)
            self.assertNotIn("AUTO_AXIOM_CHECK_MARKER", source.read_text())

            explicit = subprocess.run(
                ["bash", str(_script_path()), str(snapshot), "--report-only"],
                cwd=project,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(explicit.returncode, 0, explicit.stderr)
            self.assertEqual(calls.read_text().splitlines(), checked)


class PlanInjectionTest(unittest.TestCase):
    def _state_with_report(self, rep: AxiomSweepReport) -> Path:
        d = tempfile.mkdtemp()
        state = Path(d)
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        iterlog = state / "logs" / "iter-002"
        iterlog.mkdir(parents=True)
        write_reports(rep, iterlog, state)
        return state

    def test_launderings_injected_for_next_iter(self):
        state = self._state_with_report(
            AxiomSweepReport(findings=[AxiomFinding("A.b", "sorryAx")], ran=True)
        )
        block = _axiom_sweep_findings_block(state, 3)  # iter 3 reads iter-002
        self.assertIn("sorryAx laundering", block)
        self.assertIn("A.b", block)

    def test_no_launderings_is_empty_block(self):
        state = self._state_with_report(
            AxiomSweepReport(findings=[AxiomFinding("A.c", "myAx")], ran=True)
        )
        self.assertEqual(_axiom_sweep_findings_block(state, 3), "")

    def test_iter_one_is_empty(self):
        state = self._state_with_report(
            AxiomSweepReport(findings=[AxiomFinding("A.b", "sorryAx")], ran=True)
        )
        self.assertEqual(_axiom_sweep_findings_block(state, 1), "")


if __name__ == "__main__":
    unittest.main()
