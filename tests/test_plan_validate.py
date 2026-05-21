"""Tests for the plan-validate hook.

Covers:

* The intentional no-prover skip marker is recognized inside
  ``## Current Objectives`` and treated as a legitimate state.
* Without the marker (and no parseable objectives), the validator
  appends a discuss-format hint and returns False.
* The hint format is single-line, discuss-compatible.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archon.commands.loop.plan_validate import (
    _append_hint,
    _append_overcap_hint,
    _has_intentional_skip_marker,
    _INTENTIONAL_SKIP_RE,
    _rel_to_project,
    validate_plan_output,
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
                line for line in body.splitlines()
                if line.strip().startswith("- [")
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


class AppendOvercapHintFormatTest(unittest.TestCase):
    """The over-cap hint must list every deferred file so the next plan
    agent can re-prioritize without round-tripping through meta.json."""

    def test_hint_lists_deferred_files(self):
        with tempfile.TemporaryDirectory() as d:
            hints = Path(d) / "USER_HINTS.md"
            _append_overcap_hint(
                hints,
                cap=10,
                proposed=13,
                deferred_rels=["Foo.lean", "Bar/Baz.lean", "Quux.lean"],
            )
            body = hints.read_text(encoding="utf-8")
            self.assertIn("over the dispatch cap of 10", body)
            self.assertIn("13 objectives", body)
            self.assertIn("- Foo.lean", body)
            self.assertIn("- Bar/Baz.lean", body)
            self.assertIn("- Quux.lean", body)
            self.assertIn("archon[plan-validate]", body)
            # Discuss-format timestamped bullet on the header line.
            self.assertRegex(
                body,
                r"- \[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\] "
                r"archon\[plan-validate\]:",
            )

    def test_hint_appends_to_existing_content(self):
        with tempfile.TemporaryDirectory() as d:
            hints = Path(d) / "USER_HINTS.md"
            hints.write_text("# User Hints\n\nprior content\n", encoding="utf-8")
            _append_overcap_hint(
                hints, cap=10, proposed=11, deferred_rels=["Foo.lean"],
            )
            body = hints.read_text(encoding="utf-8")
            self.assertTrue(body.startswith("# User Hints"))
            self.assertIn("prior content", body)
            self.assertIn("Foo.lean", body)


class RelToProjectTest(unittest.TestCase):
    def test_path_under_project_relativized(self):
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d).resolve()
            (proj / "src").mkdir()
            target = proj / "src" / "Foo.lean"
            target.write_text("")
            self.assertEqual(
                _rel_to_project(target, proj),
                "src/Foo.lean",
            )

    def test_path_outside_project_falls_back_to_str(self):
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d).resolve() / "proj"
            proj.mkdir()
            # An absolute path outside the project (e.g. parent dir)
            outside = proj.parent / "external.lean"
            self.assertEqual(_rel_to_project(outside, proj), str(outside))


class ValidatePlanOutputOvercapTest(unittest.TestCase):
    """Integration: validate_plan_output truncates dispatch + hints on overcap."""

    def _make_ctx(
        self,
        project_path: Path,
        state_dir: Path,
        max_objectives: int,
        *,
        block_on_blocked_deps: bool = False,
    ):
        """Build a minimal LoopContext-shaped object.

        validate_plan_output reads a small surface of ctx — dry_run,
        skip_now, iter_meta, progress_file, project_path, state_dir,
        options.max_objectives, options.block_on_blocked_deps. A
        SimpleNamespace covers it; we don't need the full LoopContext
        dataclass for this test.

        ``block_on_blocked_deps`` defaults to False here so the
        existing over-cap tests don't accidentally exercise the filter
        path (their fixtures are bare files with no log).
        """
        from types import SimpleNamespace
        return SimpleNamespace(
            dry_run=False,
            skip_now=set(),
            iter_num=42,
            iter_meta=state_dir / "logs" / "iter-042" / "meta.json",
            project_path=project_path,
            state_dir=state_dir,
            progress_file=state_dir / "PROGRESS.md",
            options=SimpleNamespace(
                max_objectives=max_objectives,
                block_on_blocked_deps=block_on_blocked_deps,
            ),
        )

    def test_overcap_truncates_and_hints(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            state = root / ".archon"
            state.mkdir()
            (state / "logs" / "iter-042").mkdir(parents=True)

            # Create 13 .lean files and reference them in PROGRESS.md.
            files = [f"F{i:02d}.lean" for i in range(13)]
            for name in files:
                (root / name).write_text("def x : True := trivial\n")
            lines = ["# Progress", "", "## Current Objectives", ""]
            for i, name in enumerate(files, start=1):
                lines.append(f"{i}. **`{name}`** — Fill sorry.")
            (state / "PROGRESS.md").write_text("\n".join(lines) + "\n")

            ctx = self._make_ctx(root, state, max_objectives=10)
            result = validate_plan_output(ctx)

            self.assertTrue(result)
            # The hint file lists the 3 deferred files.
            hints_body = (state / "USER_HINTS.md").read_text(encoding="utf-8")
            self.assertIn("over the dispatch cap of 10", hints_body)
            self.assertIn("13 objectives", hints_body)
            for deferred_name in files[10:]:
                self.assertIn(deferred_name, hints_body)
            # The first-10 files are NOT in the hint.
            for kept_name in files[:10]:
                self.assertNotIn(f"  - {kept_name}", hints_body)

    def test_within_cap_no_hint(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            state = root / ".archon"
            state.mkdir()
            (state / "logs" / "iter-042").mkdir(parents=True)

            (root / "Foo.lean").write_text("def x : True := trivial\n")
            (state / "PROGRESS.md").write_text(
                "# Progress\n\n"
                "## Current Objectives\n\n"
                "1. **`Foo.lean`** — Fill sorry.\n"
            )

            ctx = self._make_ctx(root, state, max_objectives=10)
            result = validate_plan_output(ctx)

            self.assertTrue(result)
            self.assertFalse((state / "USER_HINTS.md").exists())


class ValidatePlanOutputBlockedDepsTest(unittest.TestCase):
    """Integration: blocked-deps filter goes through validate_plan_output."""

    def _make_ctx(self, project_path: Path, state_dir: Path):
        from types import SimpleNamespace
        return SimpleNamespace(
            dry_run=False,
            skip_now=set(),
            iter_num=42,
            iter_meta=state_dir / "logs" / "iter-042" / "meta.json",
            project_path=project_path,
            state_dir=state_dir,
            progress_file=state_dir / "PROGRESS.md",
            options=SimpleNamespace(
                max_objectives=10,
                block_on_blocked_deps=True,
            ),
        )

    def _setup_project(self, root: Path) -> Path:
        state = root / ".archon"
        state.mkdir()
        (state / "logs" / "iter-042").mkdir(parents=True)
        # Chain: Downstream.lean imports Upstream.lean.
        (root / "Upstream.lean").write_text(
            "def x : True := trivial\n", encoding="utf-8",
        )
        (root / "Downstream.lean").write_text(
            "import Upstream\n", encoding="utf-8",
        )
        return state

    def test_downstream_dropped_when_upstream_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            state = self._setup_project(root)
            # Lake build failed last iter on Upstream.lean.
            (state / "last_lake_build.log").write_text(
                "error: Upstream.lean:1:1: bad\n", encoding="utf-8",
            )
            # Planner listed only Downstream — Upstream isn't being
            # fixed this iter, so Downstream is dropped.
            (state / "PROGRESS.md").write_text(
                "# Progress\n\n## Current Objectives\n\n"
                "1. **`Downstream.lean`** — Fill sorry.\n",
            )
            ctx = self._make_ctx(root, state)
            result = validate_plan_output(ctx)
            # Every objective was dropped → False (no prover this iter).
            self.assertFalse(result)
            hints = (state / "USER_HINTS.md").read_text(encoding="utf-8")
            self.assertIn("Downstream.lean", hints)
            self.assertIn("Upstream.lean", hints)

    def test_downstream_kept_when_upstream_also_objective(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            state = self._setup_project(root)
            (state / "last_lake_build.log").write_text(
                "error: Upstream.lean:1:1: bad\n", encoding="utf-8",
            )
            # Planner listed BOTH — Upstream is presumed-being-fixed.
            (state / "PROGRESS.md").write_text(
                "# Progress\n\n## Current Objectives\n\n"
                "1. **`Upstream.lean`** — Fix the compile error.\n"
                "2. **`Downstream.lean`** — Fill sorry once Upstream works.\n",
            )
            ctx = self._make_ctx(root, state)
            result = validate_plan_output(ctx)
            self.assertTrue(result)
            # No USER_HINTS line written — nothing was dropped.
            self.assertFalse((state / "USER_HINTS.md").exists())

    def test_no_log_means_no_filter(self):
        # First-iter / no-prior-failure case: no log file, no blocked
        # set, no filtering.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            state = self._setup_project(root)
            (state / "PROGRESS.md").write_text(
                "# Progress\n\n## Current Objectives\n\n"
                "1. **`Downstream.lean`** — Fill sorry.\n",
            )
            ctx = self._make_ctx(root, state)
            result = validate_plan_output(ctx)
            self.assertTrue(result)
            self.assertFalse((state / "USER_HINTS.md").exists())

    def test_partial_filter_keeps_unblocked_and_hints_dropped(self):
        # Mix: one objective is downstream-of-blocked (drop), one is
        # independent (keep). The iter proceeds with the independent one.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            state = self._setup_project(root)
            (root / "Independent.lean").write_text(
                "def y : True := trivial\n", encoding="utf-8",
            )
            (state / "last_lake_build.log").write_text(
                "error: Upstream.lean:1:1: bad\n", encoding="utf-8",
            )
            (state / "PROGRESS.md").write_text(
                "# Progress\n\n## Current Objectives\n\n"
                "1. **`Downstream.lean`** — Fill sorry.\n"
                "2. **`Independent.lean`** — Fill sorry.\n",
            )
            ctx = self._make_ctx(root, state)
            result = validate_plan_output(ctx)
            self.assertTrue(result)
            hints = (state / "USER_HINTS.md").read_text(encoding="utf-8")
            self.assertIn("Downstream.lean", hints)
            self.assertNotIn("Independent.lean", hints)


if __name__ == "__main__":
    unittest.main()
