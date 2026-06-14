"""Tests for `.archon/peers.yaml` parsing and peer resolution."""

import json
import tempfile
import unittest
from pathlib import Path

from archon.commands.tooling import peers


def _make_project(root: Path, name: str, *, archon: bool = True, dag: bool = False) -> Path:
    p = root / name
    p.mkdir(parents=True)
    if archon:
        (p / ".archon").mkdir()
    if dag:
        (p / ".leandag").mkdir()
        (p / ".leandag" / "dag.json").write_text("{}", encoding="utf-8")
    return p


def _write_peers(project: Path, body: str) -> None:
    (project / ".archon").mkdir(parents=True, exist_ok=True)
    (project / ".archon" / "peers.yaml").write_text(body, encoding="utf-8")


class PeersConfigTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.proj = _make_project(self.root, "main")

    def tearDown(self):
        self._td.cleanup()

    def test_no_file_inactive(self):
        cfg = peers.load(self.proj)
        self.assertFalse(cfg.is_active)
        self.assertFalse(peers.is_enabled(self.proj))
        self.assertEqual(peers.resolve_peers(self.proj), [])

    def test_empty_read_inactive(self):
        _write_peers(self.proj, "read: []\nno-access: []\n")
        self.assertFalse(peers.is_enabled(self.proj))

    def test_unknown_key_reported(self):
        _write_peers(self.proj, "read: []\nbogus: 1\n")
        _, problems = peers.load_with_problems(self.proj)
        self.assertTrue(any("bogus" in p for p in problems))

    def test_resolves_archon_projects_only(self):
        _make_project(self.root, "peerA", dag=True)
        _make_project(self.root, "peerB", dag=False)
        _make_project(self.root, "notproj", archon=False)
        _write_peers(self.proj, f"read:\n  - {self.root}/*\n")
        got = {p.name for p in peers.resolve_peers(self.proj)}
        self.assertEqual(got, {"peerA", "peerB"})  # notproj + self excluded

    def test_deny_wins(self):
        _make_project(self.root, "peerA", dag=True)
        _make_project(self.root, "peerB", dag=True)
        _write_peers(self.proj, f"read:\n  - {self.root}/peer*\nno-access:\n  - {self.root}/peerB\n")
        got = {p.name for p in peers.resolve_peers(self.proj)}
        self.assertEqual(got, {"peerA"})

    def test_has_dag_flag(self):
        _make_project(self.root, "withdag", dag=True)
        _make_project(self.root, "nodag", dag=False)
        _write_peers(self.proj, f"read:\n  - {self.root}/*\n")
        by_name = {p.name: p for p in peers.resolve_peers(self.proj)}
        self.assertTrue(by_name["withdag"].has_dag)
        self.assertFalse(by_name["nodag"].has_dag)

    def test_self_excluded_even_if_matched(self):
        _write_peers(self.proj, f"read:\n  - {self.root}/*\n")
        names = {p.name for p in peers.resolve_peers(self.proj)}
        self.assertNotIn("main", names)


class PeerDagReadTest(unittest.TestCase):
    def test_available_and_needed_names(self):
        dag = {"nodes": [
            {"lean_name": "A.foo", "proved": True},
            {"lean_name": "A.bar", "mathlib_ok": True},
            {"lean_name": "A.baz", "proved": False},
            {"lean_name": "A.one, A.two", "proved": True},  # multi-decl node
        ]}
        self.assertEqual(peers.available_lean_names(dag), {"A.foo", "A.bar", "A.one", "A.two"})
        self.assertEqual(peers.needed_lean_names(dag), {"A.baz"})

    def test_read_peer_dag_missing(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(peers.read_peer_dag(td))


class TemplateTest(unittest.TestCase):
    def test_template_is_inactive(self):
        with tempfile.TemporaryDirectory() as td:
            proj = _make_project(Path(td), "p")
            peers.write_template(proj)
            # The shipped template scopes nothing until edited.
            self.assertFalse(peers.is_enabled(proj))


if __name__ == "__main__":
    unittest.main()
