"""Tests for the auto-injected prompt blocks the loop replaces agent file-reads with.

The user's design principle: anything the loop can do deterministically
should be done in code and *injected* into the prompt — not asked of
the agent as "go read file X then clear it". Two blocks under test:

* ``_user_hints_block`` — renders the captured USER_HINTS.md text
  inline (the loop captures + clears the file; the agent never sees
  the file system path).
* ``_blueprint_doctor_findings_block`` — reads the prior iter's
  ``blueprint-doctor.json`` and renders the live findings inline so
  the agent doesn't have to open the report.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from archon.prompts import (
    _blueprint_doctor_findings_block,
    _user_hints_block,
    build_plan_prompt,
)


class UserHintsBlockTest(unittest.TestCase):
    def test_empty_renders_fallback_affordance(self):
        block = _user_hints_block(None)
        self.assertIn("## User hints", block)
        self.assertIn("No user hints this iteration", block)
        # The fallback rule appears wrapped — collapse whitespace
        # when checking the phrase.
        normalized = " ".join(block.split())
        self.assertIn("Fallback if no user response", normalized)

    def test_empty_string_treated_as_no_hints(self):
        self.assertIn("No user hints", _user_hints_block(""))
        self.assertIn("No user hints", _user_hints_block("   \n  \n"))

    def test_non_empty_renders_captured_text(self):
        hints = "- [2026-05-18T12:00:00Z] focus on the M2.a route this iter"
        block = _user_hints_block(hints)
        self.assertIn("## User hints", block)
        self.assertIn("focus on the M2.a route this iter", block)
        # The block must declare that the loop will clear the file —
        # otherwise the agent re-reads + re-clears it (defeats the move).
        self.assertIn("clear", block.lower())
        self.assertIn("do NOT need to read", block)


class BlueprintDoctorFindingsBlockTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.state = Path(self._td.name) / ".archon"
        self.state.mkdir()

    def tearDown(self):
        self._td.cleanup()

    def _write_prior_doctor(self, iter_num: int, payload: dict) -> Path:
        prev = iter_num - 1
        d = self.state / "logs" / f"iter-{prev:03d}"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "blueprint-doctor.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        return p

    def test_empty_when_iter_one(self):
        # No prior iter — block stays empty.
        self.assertEqual(
            _blueprint_doctor_findings_block(self.state, 1), "",
        )

    def test_empty_when_prior_file_missing(self):
        self.assertEqual(
            _blueprint_doctor_findings_block(self.state, 5), "",
        )

    def test_empty_when_no_findings_in_prior(self):
        self._write_prior_doctor(5, {
            "orphan_chapters": [],
            "broken_refs": [],
            "axiom_decls": [],
        })
        self.assertEqual(
            _blueprint_doctor_findings_block(self.state, 5), "",
        )

    def test_renders_orphans(self):
        self._write_prior_doctor(5, {
            "orphan_chapters": [
                "/proj/blueprint/src/chapters/Foo.tex",
                "/proj/blueprint/src/chapters/Bar.tex",
            ],
            "broken_refs": [],
            "axiom_decls": [],
        })
        block = _blueprint_doctor_findings_block(self.state, 5)
        self.assertIn("## Blueprint doctor", block)
        self.assertIn("Orphan chapters", block)
        self.assertIn("Foo.tex", block)
        self.assertIn("Bar.tex", block)

    def test_renders_broken_refs_grouped_by_chapter(self):
        self._write_prior_doctor(5, {
            "orphan_chapters": [],
            "broken_refs": [
                {"chapter": "/proj/blueprint/src/chapters/A.tex",
                 "kind": "ref", "label": "thm:missing"},
                {"chapter": "/proj/blueprint/src/chapters/A.tex",
                 "kind": "uses", "label": "lem:also_missing"},
                {"chapter": "/proj/blueprint/src/chapters/B.tex",
                 "kind": "cref", "label": "def:gone"},
            ],
            "axiom_decls": [],
        })
        block = _blueprint_doctor_findings_block(self.state, 5)
        self.assertIn("Broken cross-references", block)
        self.assertIn("thm:missing", block)
        self.assertIn("lem:also_missing", block)
        self.assertIn("def:gone", block)
        # Grouped by chapter — A.tex appears once, with its two refs
        # underneath as sub-bullets.
        a_count = block.count("A.tex")
        self.assertEqual(a_count, 1, f"expected A.tex grouped once, got {a_count}")

    def test_renders_axiom_decls(self):
        self._write_prior_doctor(5, {
            "orphan_chapters": [],
            "broken_refs": [],
            "axiom_decls": [
                {"file": "/proj/Foo.lean", "name": "magic_axiom"},
            ],
        })
        block = _blueprint_doctor_findings_block(self.state, 5)
        self.assertIn("Axiom declarations", block)
        self.assertIn("magic_axiom", block)
        self.assertIn("no new axioms", block.lower())

    def test_caps_huge_orphan_list(self):
        payload = {
            "orphan_chapters": [f"/p/chapters/Orphan{i:03d}.tex" for i in range(50)],
            "broken_refs": [],
            "axiom_decls": [],
        }
        self._write_prior_doctor(5, payload)
        block = _blueprint_doctor_findings_block(
            self.state, 5, max_orphans=10,
        )
        # First 10 entries are rendered (0-indexed 000..009).
        self.assertIn("Orphan000.tex", block)
        self.assertIn("Orphan009.tex", block)
        # The 11th entry (010) is beyond the cap.
        self.assertNotIn("Orphan010.tex", block)
        self.assertIn("and 40 more", block)


class BuildPlanPromptIntegrationTest(unittest.TestCase):
    """Verify the captured hints and doctor findings flow through
    ``build_plan_prompt`` and land in the final prompt text."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.state = self.root / ".archon"
        self.state.mkdir()
        (self.state / "subagents").mkdir()

    def tearDown(self):
        self._td.cleanup()

    def test_captured_hints_appear_in_prompt(self):
        hints_text = "- [ts] please focus on M2.a this iter"
        prompt = build_plan_prompt(
            project_name="proj",
            project_path=self.root,
            state_dir=self.state,
            stage="prover",
            iter_num=7,
            captured_user_hints=hints_text,
        )
        self.assertIn("## User hints", prompt)
        self.assertIn("please focus on M2.a this iter", prompt)
        # Boilerplate note that the loop manages the file:
        self.assertIn("loop will clear", prompt.lower())

    def test_no_hints_renders_fallback_block(self):
        prompt = build_plan_prompt(
            project_name="proj",
            project_path=self.root,
            state_dir=self.state,
            stage="prover",
            iter_num=7,
            captured_user_hints=None,
        )
        self.assertIn("No user hints this iteration", prompt)

    def test_doctor_findings_inline_in_prompt(self):
        # Write a prior-iter sidecar so the doctor block fires.
        prev = self.state / "logs" / "iter-006"
        prev.mkdir(parents=True)
        (prev / "blueprint-doctor.json").write_text(json.dumps({
            "orphan_chapters": ["/p/chapters/Stale.tex"],
            "broken_refs": [],
            "axiom_decls": [],
        }), encoding="utf-8")
        prompt = build_plan_prompt(
            project_name="proj",
            project_path=self.root,
            state_dir=self.state,
            stage="prover",
            iter_num=7,
        )
        self.assertIn("## Blueprint doctor", prompt)
        self.assertIn("Stale.tex", prompt)


if __name__ == "__main__":
    unittest.main()
