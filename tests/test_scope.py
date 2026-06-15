"""Tests for scope resolution, the merge DAG, and the roadmap analysis."""

import json
import tempfile
import unittest
from pathlib import Path

from archon.commands.scope import resolve as scope_mod
from archon.commands.scope.merge_dag import build_merge_dag
from archon.commands.scope.roadmap import build_roadmap


def _make_project(root: Path, name: str, *, nodes=None) -> Path:
    """An Archon project with an optional cached DAG of {lean_name: proved}."""
    p = root / name
    (p / ".archon").mkdir(parents=True)
    if nodes is not None:
        (p / ".leandag").mkdir()
        dag = {"nodes": [
            {"lean_name": ln, "proved": bool(proved)} for ln, proved in nodes.items()
        ]}
        (p / ".leandag" / "dag.json").write_text(json.dumps(dag), encoding="utf-8")
    return p


class ScopeResolveTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_not_a_scope_without_manifest(self):
        self.assertFalse(scope_mod.is_scope(self.root))
        self.assertEqual(scope_mod.resolve_members(self.root), [])

    def test_init_scaffolds_manifest_and_dir(self):
        scope_mod.write_template(self.root)
        self.assertTrue(scope_mod.is_scope(self.root))
        self.assertTrue(scope_mod.scope_dir(self.root).is_dir())
        # Template scopes nothing until edited.
        self.assertEqual(scope_mod.resolve_members(self.root), [])

    def test_resolves_members_inside_and_glob(self):
        _make_project(self.root, "a", nodes={"X": True})
        _make_project(self.root, "b", nodes={"X": False})
        (self.root / "notproj").mkdir()
        scope_mod.scope_peers_path(self.root).write_text("read:\n  - ./*\n", encoding="utf-8")
        names = {m.name for m in scope_mod.resolve_members(self.root)}
        self.assertEqual(names, {"a", "b"})  # notproj excluded (no .archon)

    def test_external_member_path(self):
        # A member living outside the scope folder.
        with tempfile.TemporaryDirectory() as other:
            ext = _make_project(Path(other), "ext", nodes={"X": True})
            scope_mod.scope_peers_path(self.root).write_text(
                f"read:\n  - {ext}\n", encoding="utf-8")
            self.assertEqual({m.name for m in scope_mod.resolve_members(self.root)}, {"ext"})


class MergeDagTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_project(self.root, "a", nodes={"Shared": True, "OnlyA": True})
        _make_project(self.root, "b", nodes={"Shared": False, "OnlyB": False})
        scope_mod.scope_peers_path(self.root).write_text("read:\n  - ./*\n", encoding="utf-8")
        self.members = scope_mod.resolve_members(self.root)

    def tearDown(self):
        self._td.cleanup()

    def test_union_and_provenance(self):
        mdag = build_merge_dag(self.members)
        by = {n.lean_name: n for n in mdag.nodes}
        self.assertEqual(set(by), {"Shared", "OnlyA", "OnlyB"})
        shared = by["Shared"]
        self.assertEqual(set(shared.declared_by), {"a", "b"})
        self.assertEqual(shared.satisfied_by, ["a"])   # a proved it
        self.assertEqual(shared.needed_by, ["b"])      # b still needs it
        self.assertTrue(shared.shared)
        self.assertFalse(by["OnlyA"].shared)


class RoadmapTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _scope(self, **projects):
        for name, nodes in projects.items():
            _make_project(self.root, name, nodes=nodes)
        scope_mod.scope_peers_path(self.root).write_text("read:\n  - ./*\n", encoding="utf-8")
        return scope_mod.resolve_members(self.root)

    def test_unblock_and_leverage(self):
        # a has Shared proved; b needs Shared → a unblocks b.
        members = self._scope(a={"Shared": True}, b={"Shared": False, "Own": False})
        rm, _ = build_roadmap(members)
        edges = {(e.provider, e.consumer): e.names for e in rm.unblock}
        self.assertIn(("a", "b"), edges)
        self.assertEqual(edges[("a", "b")], ["Shared"])
        top = rm.leverage[0]
        self.assertEqual(top.member, "a")
        self.assertEqual(top.unblock_count, 1)

    def test_duplication_detected(self):
        # Both b and c still need Dup → duplicated work.
        members = self._scope(
            a={"Dup": True}, b={"Dup": False}, c={"Dup": False})
        rm, _ = build_roadmap(members)
        dups = {d.lean_name: d for d in rm.duplication}
        self.assertIn("Dup", dups)
        self.assertEqual(set(dups["Dup"].needed_by), {"b", "c"})
        self.assertEqual(dups["Dup"].satisfied_by, ["a"])

    def test_stalled_member(self):
        # b's only open name is satisfied by a → b is stalled (fully unblockable).
        members = self._scope(a={"Shared": True}, b={"Shared": False})
        rm, _ = build_roadmap(members)
        self.assertIn("b", rm.stalled)
        self.assertNotIn("a", rm.stalled)

    def test_no_dag_member_listed(self):
        _make_project(self.root, "a", nodes={"X": True})
        _make_project(self.root, "nodag", nodes=None)  # no .leandag
        scope_mod.scope_peers_path(self.root).write_text("read:\n  - ./*\n", encoding="utf-8")
        rm, _ = build_roadmap(scope_mod.resolve_members(self.root))
        self.assertIn("nodag", rm.no_dag)


if __name__ == "__main__":
    unittest.main()
