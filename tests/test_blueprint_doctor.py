"""Tests for the blueprint-doctor structural lint.

The doctor runs between prover and review each iter. It detects:
1. Orphan chapters — .tex files under blueprint/src/chapters/ not
   reachable from content.tex via \\input (directly or transitively).
2. Broken cross-references — \\ref{...} / \\uses{...} / \\cref{...}
   targets that no \\label{...} defines anywhere in the included tex
   tree.
Both are deterministic; the doctor never mutates the blueprint.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archon.commands.loop.blueprint_doctor import (
    _strip_tex_comments,
    run_blueprint_doctor,
    write_reports,
)


class StripTexCommentsTest(unittest.TestCase):
    def test_strips_basic_line_comment(self):
        self.assertEqual(
            _strip_tex_comments("alpha % comment text"),
            "alpha ",
        )

    def test_preserves_escaped_percent(self):
        self.assertEqual(
            _strip_tex_comments(r"50\% off % real comment"),
            r"50\% off ",
        )

    def test_strips_comment_at_line_start(self):
        self.assertEqual(
            _strip_tex_comments("% \\input{chapters/Orphan}\nactual line"),
            "\nactual line",
        )

    def test_preserves_lines_without_comments(self):
        text = "line1\nline2\nline3"
        self.assertEqual(_strip_tex_comments(text), text)


class _BlueprintProject:
    """Construct a temp project with a blueprint layout for the doctor.

    Default layout: content.tex \\inputs `macros/common` + `chapters/Good`;
    chapters/Orphan.tex exists but is not \\input'd (or is commented out).
    Good.tex has matching \\label / \\ref pairs; broken refs are
    added by the per-test caller via ``write_chapter``.
    """

    def __init__(self, root: Path):
        self.root = root
        self.src = root / "blueprint" / "src"
        self.chapters = self.src / "chapters"
        self.macros = self.src / "macros"
        self.chapters.mkdir(parents=True)
        self.macros.mkdir(parents=True)
        (self.macros / "common.tex").write_text("", encoding="utf-8")
        # Default content.tex with one included chapter and a commented-out
        # \\input for the orphan to exercise the comment stripper.
        (self.src / "content.tex").write_text(
            "\\input{macros/common}\n"
            "\\input{chapters/Good}\n"
            "% \\input{chapters/Orphan}\n",
            encoding="utf-8",
        )

    def write_chapter(self, name: str, body: str) -> Path:
        path = self.chapters / f"{name}.tex"
        path.write_text(body, encoding="utf-8")
        return path

    def include_chapter(self, name: str) -> None:
        content = (self.src / "content.tex").read_text(encoding="utf-8")
        content = content + f"\\input{{chapters/{name}}}\n"
        (self.src / "content.tex").write_text(content, encoding="utf-8")


class RunBlueprintDoctorTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.bp = _BlueprintProject(self.root)

    def tearDown(self):
        self._td.cleanup()

    def test_returns_none_when_no_blueprint(self):
        # Empty dir — no blueprint/src/.
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(run_blueprint_doctor(Path(d)))

    def test_clean_project_has_no_findings(self):
        self.bp.write_chapter(
            "Good",
            "\\begin{theorem}\\label{thm:foo}A.\\end{theorem}\n"
            "\\cref{thm:foo}\n",
        )
        # No Orphan.tex on disk → no orphans.
        r = run_blueprint_doctor(self.root)
        self.assertIsNotNone(r)
        self.assertFalse(r.has_findings)
        self.assertEqual(r.orphan_chapters, [])
        self.assertEqual(r.broken_refs, [])

    def test_detects_literal_ref_placeholders(self):
        self.bp.write_chapter(
            "Good",
            "\\begin{theorem}\\label{thm:foo}\n"
            "In the setting of Definition~REF, see Sections REF--REF.\n"
            "\\end{theorem}\n"
            "\\cref{thm:foo}\n",
        )
        r = run_blueprint_doctor(self.root)
        lits = [(k, reason) for _, k, reason in r.malformed_refs if k == "literal-ref"]
        self.assertEqual(len(lits), 3)
        self.assertIn("Definition~REF", lits[0][1])

    def test_no_literal_ref_false_positives(self):
        self.bp.write_chapter(
            "Good",
            "\\begin{theorem}\\label{thm:REF_like}\n"
            "PREFIX and REFEREE and \\cref{thm:REF_like} are fine.\n"
            "\\end{theorem}\n",
        )
        r = run_blueprint_doctor(self.root)
        self.assertEqual(
            [k for _, k, _ in r.malformed_refs if k == "literal-ref"], [],
        )

    def test_detects_interleaved_math_delimiters(self):
        self.bp.write_chapter(
            "Good",
            "\\begin{theorem}\\label{thm:foo}\n"
            "Let $P \\subseteq Q\\( be the subsheaf whose \\)T$-points vanish.\n"
            "Then \\(H^1(C, \\mathcal{O}) = 0\\) and $x = y$.\n"
            "\\end{theorem}\n",
        )
        r = run_blueprint_doctor(self.root)
        probs = [reason for _, k, reason in r.malformed_refs if k == "math-delim"]
        self.assertTrue(any("inside $…$" in p for p in probs), probs)
        # The well-formed formulas on the next line produce no findings.
        self.assertTrue(all("line 3" not in p for p in probs), probs)

    def test_detects_bare_labels_in_prose(self):
        self.bp.write_chapter(
            "Good",
            "\\begin{theorem}\\label{thm:foo}\\uses{lem:helper}\n"
            "By Kleiman Thm.~th:main the claim follows; see \\cref{thm:foo}.\n"
            "\\end{theorem}\n"
            "\\begin{lemma}\\label{lem:helper}H.\\end{lemma}\n",
        )
        r = run_blueprint_doctor(self.root)
        bare = [reason for _, k, reason in r.malformed_refs if k == "bare-label"]
        self.assertEqual(len(bare), 1, bare)
        self.assertIn("th:main", bare[0])
        # labels inside \uses{} / \cref{} / \label{} are never flagged.
        self.assertNotIn("lem:helper", " ".join(bare))
        self.assertNotIn("thm:foo", " ".join(bare))

    def test_detects_orphan_chapter(self):
        self.bp.write_chapter(
            "Good",
            "\\begin{theorem}\\label{thm:foo}A.\\end{theorem}\n",
        )
        self.bp.write_chapter(
            "Orphan",
            "\\begin{remark}Stuff.\\end{remark}\n",
        )
        r = run_blueprint_doctor(self.root)
        self.assertIsNotNone(r)
        self.assertEqual(len(r.orphan_chapters), 1)
        self.assertEqual(r.orphan_chapters[0].name, "Orphan.tex")
        self.assertTrue(r.has_findings)

    def test_detects_broken_ref(self):
        self.bp.write_chapter(
            "Good",
            "\\begin{theorem}\\label{thm:foo}A.\\end{theorem}\n"
            "\\ref{thm:nonexistent}\n",
        )
        r = run_blueprint_doctor(self.root)
        self.assertIsNotNone(r)
        self.assertEqual(len(r.broken_refs), 1)
        chapter, kind, label = r.broken_refs[0]
        self.assertEqual(chapter.name, "Good.tex")
        self.assertEqual(kind, "ref")
        self.assertEqual(label, "thm:nonexistent")

    def test_detects_broken_uses_with_multiple_labels(self):
        # \uses{good, bad} — only ``bad`` is broken.
        self.bp.write_chapter(
            "Good",
            "\\begin{theorem}\\label{thm:good}A.\\end{theorem}\n"
            "\\begin{theorem}\\label{thm:foo}\\uses{thm:good, thm:bad}B.\\end{theorem}\n",
        )
        r = run_blueprint_doctor(self.root)
        self.assertIsNotNone(r)
        labels = {label for _, _, label in r.broken_refs}
        self.assertEqual(labels, {"thm:bad"})

    def test_detects_empty_uses(self):
        # \uses{} — empty argument crashes plastex's depgraph builder.
        self.bp.write_chapter(
            "Good",
            "\\begin{theorem}\\label{thm:foo}\\uses{}A.\\end{theorem}\n",
        )
        r = run_blueprint_doctor(self.root)
        self.assertIsNotNone(r)
        self.assertEqual(len(r.malformed_refs), 1)
        chapter, kind, reason = r.malformed_refs[0]
        self.assertEqual(chapter.name, "Good.tex")
        self.assertEqual(kind, "uses")
        self.assertEqual(reason, "empty argument")
        # Empty \uses{} must NOT bleed into broken_refs.
        self.assertEqual(r.broken_refs, [])
        self.assertTrue(r.has_findings)

    def test_detects_empty_proves(self):
        self.bp.write_chapter(
            "Good",
            "\\begin{theorem}\\label{thm:foo}\\proves{}A.\\end{theorem}\n",
        )
        r = run_blueprint_doctor(self.root)
        self.assertIsNotNone(r)
        kinds = {k for _, k, _ in r.malformed_refs}
        self.assertIn("proves", kinds)

    def test_detects_empty_label(self):
        # \label{} — empty definition. Doesn't crash plastex on its own
        # but the depgraph treats the empty key as a self-cycle node.
        self.bp.write_chapter(
            "Good",
            "\\begin{theorem}\\label{}A.\\end{theorem}\n",
        )
        r = run_blueprint_doctor(self.root)
        self.assertIsNotNone(r)
        kinds_reasons = {(k, reason) for _, k, reason in r.malformed_refs}
        self.assertEqual(kinds_reasons, {("label", "empty argument")})

    def test_detects_empty_ref(self):
        self.bp.write_chapter(
            "Good",
            "\\begin{theorem}\\label{thm:foo}A.\\end{theorem}\n"
            "\\ref{}\n",
        )
        r = run_blueprint_doctor(self.root)
        self.assertIsNotNone(r)
        kinds_reasons = {(k, reason) for _, k, reason in r.malformed_refs}
        self.assertEqual(kinds_reasons, {("ref", "empty argument")})

    def test_detects_empty_list_item_in_uses(self):
        # \uses{thm:good,,thm:other} — middle item is empty.
        self.bp.write_chapter(
            "Good",
            "\\begin{theorem}\\label{thm:good}A.\\end{theorem}\n"
            "\\begin{theorem}\\label{thm:other}B.\\end{theorem}\n"
            "\\begin{theorem}\\label{thm:foo}"
            "\\uses{thm:good,,thm:other}C.\\end{theorem}\n",
        )
        r = run_blueprint_doctor(self.root)
        self.assertIsNotNone(r)
        # The non-empty pieces still resolve (no broken refs); the empty
        # piece in the middle is recorded as malformed.
        self.assertEqual(r.broken_refs, [])
        kinds_reasons = {(k, reason) for _, k, reason in r.malformed_refs}
        self.assertEqual(kinds_reasons, {("uses", "empty list item")})

    def test_detects_trailing_comma_in_uses(self):
        # \uses{thm:good,} — trailing comma yields one empty list item.
        self.bp.write_chapter(
            "Good",
            "\\begin{theorem}\\label{thm:good}A.\\end{theorem}\n"
            "\\begin{theorem}\\label{thm:foo}"
            "\\uses{thm:good,}B.\\end{theorem}\n",
        )
        r = run_blueprint_doctor(self.root)
        self.assertIsNotNone(r)
        self.assertEqual(r.broken_refs, [])
        kinds_reasons = {(k, reason) for _, k, reason in r.malformed_refs}
        self.assertEqual(kinds_reasons, {("uses", "empty list item")})

    def test_ignores_commented_input(self):
        # The default content.tex has a commented-out \\input for Orphan;
        # the comment-stripping should keep Orphan.tex marked orphan.
        self.bp.write_chapter("Good", "\\label{thm:foo}\n")
        self.bp.write_chapter("Orphan", "\\label{thm:bar}\n")
        r = run_blueprint_doctor(self.root)
        self.assertIsNotNone(r)
        self.assertEqual([p.name for p in r.orphan_chapters], ["Orphan.tex"])

    def test_detects_axiom_declaration(self):
        self.bp.write_chapter("Good", "\\label{thm:foo}\n")
        # An axiom under the project's .lean files.
        (self.root / "Foo.lean").write_text(
            "axiom magic_axiom : True\n", encoding="utf-8",
        )
        r = run_blueprint_doctor(self.root)
        self.assertIsNotNone(r)
        self.assertEqual(len(r.axiom_decls), 1)
        path, name = r.axiom_decls[0]
        self.assertEqual(path.name, "Foo.lean")
        self.assertEqual(name, "magic_axiom")

    def test_axiom_in_comment_not_flagged(self):
        self.bp.write_chapter("Good", "\\label{thm:foo}\n")
        (self.root / "Foo.lean").write_text(
            "-- axiom foo : True (this is a NOTE, not a real axiom)\n"
            "/- axiom bar : True -/\n"
            "theorem real_theorem : True := trivial\n",
            encoding="utf-8",
        )
        r = run_blueprint_doctor(self.root)
        self.assertIsNotNone(r)
        self.assertEqual(r.axiom_decls, [])

    def test_axiom_under_lake_excluded(self):
        self.bp.write_chapter("Good", "\\label{thm:foo}\n")
        # Real .lean in project — clean.
        (self.root / "Project.lean").write_text(
            "theorem t : True := trivial\n", encoding="utf-8",
        )
        # Axiom under .lake/ — must be ignored (dep code).
        lake = self.root / ".lake" / "packages" / "mathlib"
        lake.mkdir(parents=True)
        (lake / "Foo.lean").write_text(
            "axiom dep_axiom : True\n", encoding="utf-8",
        )
        # Axiom under .archon/ — must be ignored (snapshots).
        snap = self.root / ".archon" / "logs" / "iter-001" / "snapshots"
        snap.mkdir(parents=True)
        (snap / "Foo.lean").write_text(
            "axiom snapshot_axiom : True\n", encoding="utf-8",
        )
        r = run_blueprint_doctor(self.root)
        self.assertIsNotNone(r)
        self.assertEqual(r.axiom_decls, [])

    def test_label_in_included_subfile_is_reachable(self):
        # When chapters \\input each other, labels in the leaf chapter
        # must still satisfy refs in the parent chapter (transitive
        # inclusion).
        self.bp.write_chapter(
            "Good",
            "\\input{chapters/Leaf}\n\\ref{thm:in_leaf}\n",
        )
        self.bp.write_chapter(
            "Leaf",
            "\\begin{theorem}\\label{thm:in_leaf}A.\\end{theorem}\n",
        )
        r = run_blueprint_doctor(self.root)
        self.assertIsNotNone(r)
        # Leaf is transitively included from Good (which is in content.tex),
        # so it should NOT count as orphan; ref should resolve.
        orphan_names = [p.name for p in r.orphan_chapters]
        self.assertNotIn("Leaf.tex", orphan_names)
        self.assertEqual(r.broken_refs, [])


class WriteReportsTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.bp = _BlueprintProject(self.root)

    def tearDown(self):
        self._td.cleanup()

    def test_clean_report_emits_short_message(self):
        self.bp.write_chapter("Good", "\\label{thm:foo}\n")
        r = run_blueprint_doctor(self.root)
        iter_dir = self.root / ".archon" / "logs" / "iter-001"
        json_path, md_path = write_reports(r, iter_dir, self.root)
        self.assertTrue(json_path.is_file())
        self.assertTrue(md_path.is_file())
        md = md_path.read_text(encoding="utf-8")
        self.assertIn("No structural findings", md)

    def test_findings_report_lists_orphans_and_refs(self):
        self.bp.write_chapter(
            "Good",
            "\\label{thm:foo}\n\\ref{thm:broken}\n",
        )
        self.bp.write_chapter("Orphan", "stuff\n")
        r = run_blueprint_doctor(self.root)
        iter_dir = self.root / ".archon" / "logs" / "iter-002"
        json_path, md_path = write_reports(r, iter_dir, self.root)
        md = md_path.read_text(encoding="utf-8")
        self.assertIn("## Orphan chapters", md)
        self.assertIn("Orphan.tex", md)
        self.assertIn("## Broken cross-references", md)
        self.assertIn("thm:broken", md)
        # JSON sidecar parses.
        import json
        data = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(len(data["orphan_chapters"]), 1)
        self.assertEqual(len(data["broken_refs"]), 1)

    def test_findings_report_lists_malformed_annotations(self):
        # Mix of empty argument and empty list item — both must surface
        # in the Markdown report and the JSON sidecar.
        self.bp.write_chapter(
            "Good",
            "\\begin{theorem}\\label{thm:good}A.\\end{theorem}\n"
            "\\begin{theorem}\\label{thm:other}B.\\end{theorem}\n"
            "\\begin{theorem}\\label{thm:foo}"
            "\\uses{}\\uses{thm:good,,thm:other}C.\\end{theorem}\n",
        )
        r = run_blueprint_doctor(self.root)
        iter_dir = self.root / ".archon" / "logs" / "iter-003"
        json_path, md_path = write_reports(r, iter_dir, self.root)
        md = md_path.read_text(encoding="utf-8")
        self.assertIn("## Malformed annotations", md)
        self.assertIn("empty argument", md)
        self.assertIn("empty list item", md)
        import json
        data = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(len(data["malformed_refs"]), 2)
        reasons = {entry["reason"] for entry in data["malformed_refs"]}
        self.assertEqual(reasons, {"empty argument", "empty list item"})


if __name__ == "__main__":
    unittest.main()
