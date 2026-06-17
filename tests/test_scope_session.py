"""Tests for the scope discuss prompt and scope dashboard host/peer wiring."""

import json
import tempfile
import unittest
from pathlib import Path

from archon.commands.scope import resolve as scope_mod
from archon.commands.scope.dashboard import _pick_host
from archon.commands.scope.discuss import _build_prompt
from archon.commands.scope.roadmap import build_roadmap, render_markdown


def _make_project(root: Path, name: str, *, nodes=None) -> Path:
    p = root / name
    (p / ".archon").mkdir(parents=True)
    if nodes is not None:
        (p / ".leandag").mkdir()
        dag = {"nodes": [{"lean_name": ln, "proved": bool(pr)} for ln, pr in nodes.items()]}
        (p / ".leandag" / "dag.json").write_text(json.dumps(dag), encoding="utf-8")
    return p


class ScopeSessionTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        _make_project(self.root, "core", nodes={"Shared": True})
        _make_project(self.root, "picard", nodes={"Shared": False, "Own": False})
        scope_mod.scope_peers_path(self.root).write_text("read:\n  - ./*\n", encoding="utf-8")
        self.members = scope_mod.resolve_members(self.root)

    def tearDown(self):
        self._td.cleanup()

    def test_discuss_prompt_embeds_roadmap_and_members(self):
        rm, _ = build_roadmap(self.members)
        prompt = _build_prompt(self.root, render_markdown(rm), self.members)
        self.assertIn("scope advisor", prompt)
        self.assertIn("core", prompt)
        self.assertIn("picard", prompt)
        # The deterministic unblock finding is carried into the prompt.
        self.assertIn("Unblock matrix", prompt)
        # Read-only-with-consent contract is stated.
        self.assertIn("archon peers pr", prompt)
        self.assertIn("archon peers issue", prompt)

    def test_pick_host_prefers_member_with_dag(self):
        host = _pick_host(self.members, None)
        self.assertTrue(host.has_dag)

    def test_pick_host_explicit_member(self):
        host = _pick_host(self.members, "picard")
        self.assertEqual(host.name, "picard")

    def test_pick_host_unknown_member_exits(self):
        import typer
        with self.assertRaises(typer.Exit):
            _pick_host(self.members, "nope")


if __name__ == "__main__":
    unittest.main()
