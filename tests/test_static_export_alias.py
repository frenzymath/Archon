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


if __name__ == "__main__":
    unittest.main()
