import tempfile
import unittest
from pathlib import Path

from archon.commands.dashboard.static_export import StaticDashboardExporter


class StaticExportAliasTest(unittest.TestCase):
    def test_scope_members_use_relative_paths_as_public_aliases(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            host = root / "MainProjects" / "Host"
            peer = root / "SubProjects" / "Peer"
            host.mkdir(parents=True)
            peer.mkdir(parents=True)

            exporter = StaticDashboardExporter(
                host,
                out_dir=root / "docs",
                members=[
                    {"name": "Host", "path": str(host), "has_dag": True},
                    {"name": "Peer", "path": str(peer), "has_dag": True},
                ],
                scope_path=root,
            )

            self.assertEqual(
                exporter._alias_for(str(host)),
                "MainProjects/Host",
            )
            self.assertEqual(
                exporter._alias_for(str(peer)),
                "SubProjects/Peer",
            )
            self.assertEqual(
                exporter._redact(str(peer / "Blueprint" / "chapter.md")),
                "SubProjects/Peer/Blueprint/chapter.md",
            )

    def test_external_scope_members_fall_back_to_basename(self):
        with tempfile.TemporaryDirectory() as scope_td, tempfile.TemporaryDirectory() as ext_td:
            root = Path(scope_td)
            external = Path(ext_td) / "ExternalProject"
            external.mkdir()

            exporter = StaticDashboardExporter(
                external,
                out_dir=root / "docs",
                members=[{"name": "ExternalProject", "path": str(external), "has_dag": True}],
                scope_path=root,
            )

            self.assertEqual(exporter._alias_for(str(external)), "ExternalProject")

    def test_external_members_sharing_a_basename_get_distinct_aliases(self):
        with tempfile.TemporaryDirectory() as scope_td, tempfile.TemporaryDirectory() as ext_td:
            root = Path(scope_td)
            ext = Path(ext_td)
            core_a = ext / "a" / "core"
            core_b = ext / "b" / "core"
            core_a.mkdir(parents=True)
            core_b.mkdir(parents=True)

            exporter = StaticDashboardExporter(
                root / "host",
                out_dir=root / "docs",
                members=[
                    {"name": "core", "path": str(core_a), "has_dag": True},
                    {"name": "core", "path": str(core_b), "has_dag": True},
                ],
                scope_path=root,
            )

            alias_a = exporter._alias_for(str(core_a))
            alias_b = exporter._alias_for(str(core_b))
            # Both are outside the scope dir and share the basename "core"; they
            # must not collapse to the same alias or one member's snapshot would
            # dedupe away.
            self.assertNotEqual(alias_a, alias_b)
            self.assertEqual(alias_a, "core")
            self.assertEqual(alias_b, "b/core")


if __name__ == "__main__":
    unittest.main()
