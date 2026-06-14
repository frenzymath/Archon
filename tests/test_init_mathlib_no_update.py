"""Regression tests: ``_ensure_mathlib`` must not run ``lake update`` when
Mathlib is already declared.

Bug: ``lake update`` was placed after the ``if has_mathlib / else`` block,
so it ran unconditionally on every ``archon init`` — silently bumping
``lake-manifest.json`` to the latest Mathlib even on existing projects.

Fix: when ``has_mathlib`` is True we return immediately, skipping the
update / cache / build cycle entirely.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from archon.commands.tooling.project import BootstrapOptions, ProjectBootstrap
from archon.commands.tooling.lake import LakefileInfo


def _lakefile_info(*, has_mathlib: bool) -> LakefileInfo:
    return LakefileInfo(
        path=Path("/fake/lakefile.toml"),
        kind="toml",
        has_mathlib=has_mathlib,
        project_name="FakeProject",
    )


class EnsureMathlibNoUpdateWhenAlreadyPresentTest(unittest.TestCase):
    """When Mathlib is already declared, ``lake update`` must not be called."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(_rmtree, self._tmp)
        self.project = Path(self._tmp)

    def _make_bootstrap(self, *, fetch_cache: bool = False, do_build: bool = False):
        opts = BootstrapOptions(
            init_lake=False,
            add_mathlib=True,
            init_blueprint=False,
            fetch_mathlib_cache=fetch_cache,
            do_initial_build=do_build,
        )
        bs = ProjectBootstrap(self.project, opts)
        return bs

    def test_lake_update_not_called_when_mathlib_already_present(self):
        bs = self._make_bootstrap()
        bs.lake = MagicMock()
        bs.lake.lakefile_info.return_value = _lakefile_info(has_mathlib=True)

        from archon.commands.tooling.project import BootstrapReport
        report = BootstrapReport()
        bs._ensure_mathlib(report)

        bs.lake.update.assert_not_called()

    def test_lake_add_mathlib_not_called_when_already_present(self):
        bs = self._make_bootstrap()
        bs.lake = MagicMock()
        bs.lake.lakefile_info.return_value = _lakefile_info(has_mathlib=True)

        from archon.commands.tooling.project import BootstrapReport
        report = BootstrapReport()
        bs._ensure_mathlib(report)

        bs.lake.add_mathlib_dependency.assert_not_called()

    def test_cache_get_not_called_when_mathlib_already_present(self):
        bs = self._make_bootstrap(fetch_cache=True)
        bs.lake = MagicMock()
        bs.lake.lakefile_info.return_value = _lakefile_info(has_mathlib=True)

        from archon.commands.tooling.project import BootstrapReport
        report = BootstrapReport()
        bs._ensure_mathlib(report)

        bs.lake.get_mathlib_cache.assert_not_called()

    def test_build_not_called_when_mathlib_already_present(self):
        bs = self._make_bootstrap(do_build=True)
        bs.lake = MagicMock()
        bs.lake.lakefile_info.return_value = _lakefile_info(has_mathlib=True)

        from archon.commands.tooling.project import BootstrapReport
        report = BootstrapReport()
        bs._ensure_mathlib(report)

        bs.lake.build.assert_not_called()

    def test_report_records_already_declared_message(self):
        bs = self._make_bootstrap()
        bs.lake = MagicMock()
        bs.lake.lakefile_info.return_value = _lakefile_info(has_mathlib=True)

        from archon.commands.tooling.project import BootstrapReport
        report = BootstrapReport()
        bs._ensure_mathlib(report)

        self.assertTrue(
            any("already declared" in a for a in report.actions),
            f"expected 'already declared' in actions, got: {report.actions}",
        )

    def test_did_work_stays_false_when_mathlib_already_present(self):
        bs = self._make_bootstrap()
        bs.lake = MagicMock()
        bs.lake.lakefile_info.return_value = _lakefile_info(has_mathlib=True)

        from archon.commands.tooling.project import BootstrapReport
        report = BootstrapReport()
        bs._ensure_mathlib(report)

        self.assertFalse(
            report.did_work,
            "did_work must stay False when no Mathlib was added",
        )


class EnsureMathlibRunsUpdateWhenAddingTest(unittest.TestCase):
    """When Mathlib is NOT yet declared, the full update/cache/build cycle runs."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(_rmtree, self._tmp)
        self.project = Path(self._tmp)

    def _make_bootstrap(self, *, fetch_cache: bool = True, do_build: bool = False):
        opts = BootstrapOptions(
            init_lake=False,
            add_mathlib=True,
            init_blueprint=False,
            fetch_mathlib_cache=fetch_cache,
            do_initial_build=do_build,
        )
        bs = ProjectBootstrap(self.project, opts)
        return bs

    def test_lake_update_called_after_adding_mathlib(self):
        bs = self._make_bootstrap(fetch_cache=False)
        bs.lake = MagicMock()
        bs.lake.lakefile_info.return_value = _lakefile_info(has_mathlib=False)
        bs.lake.add_mathlib_dependency.return_value = True

        from archon.commands.tooling.project import BootstrapReport
        report = BootstrapReport()
        bs._ensure_mathlib(report)

        bs.lake.update.assert_called_once()

    def test_cache_get_called_when_fetch_option_set(self):
        bs = self._make_bootstrap(fetch_cache=True)
        bs.lake = MagicMock()
        bs.lake.lakefile_info.return_value = _lakefile_info(has_mathlib=False)
        bs.lake.add_mathlib_dependency.return_value = True
        bs.lake.get_mathlib_cache.return_value = "ok"

        from archon.commands.tooling.project import BootstrapReport
        report = BootstrapReport()
        bs._ensure_mathlib(report)

        bs.lake.get_mathlib_cache.assert_called_once()

    def test_build_called_when_option_set(self):
        bs = self._make_bootstrap(do_build=True)
        bs.lake = MagicMock()
        bs.lake.lakefile_info.return_value = _lakefile_info(has_mathlib=False)
        bs.lake.add_mathlib_dependency.return_value = True
        bs.lake.get_mathlib_cache.return_value = "ok"

        from archon.commands.tooling.project import BootstrapReport
        report = BootstrapReport()
        bs._ensure_mathlib(report)

        bs.lake.build.assert_called_once()

    def test_update_not_called_if_add_raises(self):
        """If adding Mathlib fails, we must not attempt ``lake update``."""
        bs = self._make_bootstrap(fetch_cache=False)
        bs.lake = MagicMock()
        bs.lake.lakefile_info.return_value = _lakefile_info(has_mathlib=False)
        bs.lake.add_mathlib_dependency.side_effect = RuntimeError("write failed")

        from archon.commands.tooling.project import BootstrapReport
        report = BootstrapReport()
        bs._ensure_mathlib(report)

        bs.lake.update.assert_not_called()
        self.assertTrue(any("Failed" in w for w in report.warnings))


def _rmtree(path: str) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
