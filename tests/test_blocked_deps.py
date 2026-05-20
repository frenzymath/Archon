"""Tests for the blocked-deps filter.

The filter drops objectives whose transitive local imports failed the
previous lake build — a prover dispatched on such a file would fail to
load it. Verifies:

- ``parse_blocked_files_from_log``: regex extraction + project-relative
  normalization + skip of files outside the project tree.
- ``build_local_import_graph``: scans only project .lean files (skips
  ``.lake``, ``.archon``, ``.git``), resolves dotted module names back
  to project-relative paths, drops external (Mathlib) imports.
- ``transitive_blocked_deps``: BFS through the graph; the
  ``presumed_fixed`` exemption removes a blocked file from the effective
  blocked set BEFORE the walk, so its downstream is not flagged.
- ``filter_objectives_for_blocked_deps``: the full splitter, including
  the rule that a blocked file in the objective list is kept (planner
  is fixing it) and its presence doesn't drop downstream objectives in
  the same list.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archon.commands.loop.blocked_deps import (
    build_local_import_graph,
    filter_objectives_for_blocked_deps,
    parse_blocked_files_from_log,
    transitive_blocked_deps,
)


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


class ParseBlockedFilesTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name).resolve()

    def test_extracts_error_lines(self):
        # The file referenced by the error must actually exist under
        # the project — the filter normalizes via .resolve() so
        # phantom paths drop out.
        _write(self.root / "Project" / "Foo.lean", "")
        log = self.root / ".archon" / "last_lake_build.log"
        _write(log, (
            "error: Project/Foo.lean:42:7: unknown identifier 'bar'\n"
            "warning: Project/Foo.lean:99:1: declaration uses 'sorry'\n"
        ))
        out = parse_blocked_files_from_log(log, project_path=self.root)
        self.assertEqual(out, {Path("Project/Foo.lean")})

    def test_ignores_warnings(self):
        # Only `error:` lines count. A file that only emitted warnings
        # still compiled and its .olean is on disk.
        _write(self.root / "Project" / "OnlyWarns.lean", "")
        log = self.root / "log.txt"
        _write(log, (
            "warning: Project/OnlyWarns.lean:5:1: unused variable 'x'\n"
        ))
        self.assertEqual(parse_blocked_files_from_log(log, project_path=self.root), set())

    def test_drops_paths_outside_project(self):
        # An error pointing at a Mathlib mirror file is not the
        # planner's problem; the filter must drop it.
        log = self.root / "log.txt"
        _write(log, (
            "error: /opt/mathlib/Mathlib/Data/Foo.lean:1:1: bad\n"
        ))
        self.assertEqual(parse_blocked_files_from_log(log, project_path=self.root), set())

    def test_drops_paths_inside_lake_dir(self):
        # A path that resolves under .lake/ (dep cache) must be skipped
        # by the SKIP_PARTS check even when it's under the project.
        target = self.root / ".lake" / "packages" / "mathlib" / "Foo.lean"
        _write(target, "")
        log = self.root / "log.txt"
        _write(log, (
            f"error: {target.relative_to(self.root)}:1:1: bad\n"
        ))
        self.assertEqual(parse_blocked_files_from_log(log, project_path=self.root), set())

    def test_missing_log_returns_empty(self):
        # No previous lake build = nothing to filter against.
        self.assertEqual(
            parse_blocked_files_from_log(
                self.root / "does-not-exist.log",
                project_path=self.root,
            ),
            set(),
        )


class BuildImportGraphTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name).resolve()

    def test_resolves_local_imports_to_project_paths(self):
        _write(self.root / "A.lean", "")
        _write(self.root / "B.lean", "import A\n")
        graph = build_local_import_graph(self.root)
        self.assertEqual(graph[Path("B.lean")], {Path("A.lean")})
        self.assertEqual(graph[Path("A.lean")], set())

    def test_drops_external_imports(self):
        # Mathlib imports resolve to nothing in the project; they're
        # silently dropped so the blocked-deps walk only ever traverses
        # files the project actually controls.
        _write(self.root / "A.lean", "import Mathlib.Algebra.Field.Basic\n")
        graph = build_local_import_graph(self.root)
        self.assertEqual(graph[Path("A.lean")], set())

    def test_skips_lake_and_archon_dirs(self):
        # .lake and .archon must not appear in the graph.
        _write(self.root / "A.lean", "")
        _write(self.root / ".lake" / "packages" / "mathlib" / "Foo.lean", "")
        _write(self.root / ".archon" / "snapshots" / "Bar.lean", "")
        graph = build_local_import_graph(self.root)
        # Only A.lean is in the keyset.
        self.assertEqual(set(graph.keys()), {Path("A.lean")})

    def test_dotted_module_names_map_correctly(self):
        # Imports like `Algebra.WLocal` must resolve to
        # `Algebra/WLocal.lean`.
        _write(self.root / "Algebra" / "WLocal.lean", "")
        _write(self.root / "Algebra" / "WGlobal.lean", "import Algebra.WLocal\n")
        graph = build_local_import_graph(self.root)
        self.assertEqual(
            graph[Path("Algebra/WGlobal.lean")],
            {Path("Algebra/WLocal.lean")},
        )


class TransitiveBlockedDepsTest(unittest.TestCase):
    """C → B → A; blocking A blocks both B and C unless they're presumed_fixed."""

    def _graph(self) -> dict[Path, set[Path]]:
        return {
            Path("A.lean"): set(),
            Path("B.lean"): {Path("A.lean")},
            Path("C.lean"): {Path("B.lean")},
            Path("D.lean"): set(),  # independent
        }

    def test_chain_propagation(self):
        graph = self._graph()
        blocked = {Path("A.lean")}
        # C imports B which imports A → A is in C's transitive imports.
        self.assertEqual(
            transitive_blocked_deps(Path("C.lean"), graph, blocked),
            {Path("A.lean")},
        )

    def test_self_not_counted(self):
        # When the target itself is blocked, it doesn't count as its
        # own blocked dep. The caller treats "self is blocked AND in
        # objectives" separately via presumed_fixed.
        graph = self._graph()
        blocked = {Path("A.lean")}
        self.assertEqual(
            transitive_blocked_deps(Path("A.lean"), graph, blocked),
            set(),
        )

    def test_independent_file_unaffected(self):
        graph = self._graph()
        blocked = {Path("A.lean")}
        self.assertEqual(
            transitive_blocked_deps(Path("D.lean"), graph, blocked),
            set(),
        )

    def test_presumed_fixed_exempts_chain(self):
        # If A is presumed-being-fixed this iter (because it's in the
        # objective list), C should not be flagged for importing A
        # transitively.
        graph = self._graph()
        blocked = {Path("A.lean")}
        self.assertEqual(
            transitive_blocked_deps(
                Path("C.lean"), graph, blocked,
                presumed_fixed={Path("A.lean")},
            ),
            set(),
        )


class FilterObjectivesTest(unittest.TestCase):
    """End-to-end on a tiny realistic project shape."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name).resolve()
        # A trivial chain: A → B → C (C imports B imports A).
        _write(self.root / "A.lean", "def a : True := trivial\n")
        _write(self.root / "B.lean", "import A\n")
        _write(self.root / "C.lean", "import B\n")
        _write(self.root / "Indep.lean", "")
        self.graph = build_local_import_graph(self.root)

    def test_blocked_file_alone_kept(self):
        # A is blocked AND in objectives — the planner is fixing it.
        keep, drop = filter_objectives_for_blocked_deps(
            [self.root / "A.lean"],
            blocked={Path("A.lean")},
            graph=self.graph,
            project_path=self.root,
        )
        self.assertEqual(keep, [self.root / "A.lean"])
        self.assertEqual(drop, [])

    def test_downstream_dropped_when_blocker_not_in_objectives(self):
        # B imports A, A is blocked, A not in objectives → drop B.
        keep, drop = filter_objectives_for_blocked_deps(
            [self.root / "B.lean"],
            blocked={Path("A.lean")},
            graph=self.graph,
            project_path=self.root,
        )
        self.assertEqual(keep, [])
        self.assertEqual(len(drop), 1)
        dropped_path, blockers = drop[0]
        self.assertEqual(dropped_path, self.root / "B.lean")
        self.assertEqual(blockers, [Path("A.lean")])

    def test_downstream_kept_when_blocker_also_in_objectives(self):
        # A is blocked but also assigned this iter (presumed-being-fixed),
        # so B can be assigned alongside.
        keep, drop = filter_objectives_for_blocked_deps(
            [self.root / "A.lean", self.root / "B.lean"],
            blocked={Path("A.lean")},
            graph=self.graph,
            project_path=self.root,
        )
        self.assertEqual(set(keep), {self.root / "A.lean", self.root / "B.lean"})
        self.assertEqual(drop, [])

    def test_deep_chain_picks_blocker_via_transitive(self):
        # C imports B imports A; A is blocked and not in objectives.
        keep, drop = filter_objectives_for_blocked_deps(
            [self.root / "C.lean"],
            blocked={Path("A.lean")},
            graph=self.graph,
            project_path=self.root,
        )
        self.assertEqual(keep, [])
        self.assertEqual(len(drop), 1)
        self.assertEqual(drop[0][1], [Path("A.lean")])

    def test_independent_file_unaffected(self):
        keep, drop = filter_objectives_for_blocked_deps(
            [self.root / "Indep.lean", self.root / "B.lean"],
            blocked={Path("A.lean")},
            graph=self.graph,
            project_path=self.root,
        )
        # Indep doesn't import A — kept. B does — dropped.
        self.assertEqual(keep, [self.root / "Indep.lean"])
        self.assertEqual([p for p, _ in drop], [self.root / "B.lean"])

    def test_no_blocked_returns_input_unchanged(self):
        keep, drop = filter_objectives_for_blocked_deps(
            [self.root / "A.lean", self.root / "B.lean", self.root / "C.lean"],
            blocked=set(),
            graph=self.graph,
            project_path=self.root,
        )
        self.assertEqual(
            keep,
            [self.root / "A.lean", self.root / "B.lean", self.root / "C.lean"],
        )
        self.assertEqual(drop, [])


if __name__ == "__main__":
    unittest.main()
