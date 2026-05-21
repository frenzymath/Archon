"""Regression test: ``PackageDataDoctorCheck`` references real subdirs.

The v0.2.0 PR renamed the bundled ``agents/`` directory to
``subagents/`` but the doctor's ``_CHECKS`` lookup still pointed at
``agents/``, so every ``archon doctor`` invocation tacked on a
permanent ``data: agents not found`` row (counted as a ``✗ 1
error(s)`` summary line). This test pins the rename + asserts that
all entries actually resolve in the installed package data.
"""

from __future__ import annotations

import unittest

from archon.commands.doctor import PackageDataDoctorCheck


class PackageDataChecksResolveTest(unittest.TestCase):

    def test_subagents_replaces_agents(self):
        keys = PackageDataDoctorCheck._CHECKS.keys()
        self.assertIn("subagents", keys)
        self.assertNotIn("agents", keys)

    def test_every_check_target_exists_in_package_data(self):
        # Wire each entry through the real importlib.resources lookup
        # the doctor itself uses, so the test catches future drift if
        # someone renames another directory.
        from archon.commands.doctor import _data_path
        for name, sub in PackageDataDoctorCheck._CHECKS.items():
            with self.subTest(name=name):
                p = _data_path(sub)
                self.assertTrue(
                    p.exists(),
                    f"package data entry {name!r} → {sub!r} not found at {p}",
                )


if __name__ == "__main__":
    unittest.main()
