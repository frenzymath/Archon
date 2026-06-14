"""Tests for sync_leanok's compile-check warm-up.

The per-file ``lake build <module>`` compile checks populate a cache before
the sequential marker pass. They run **serially**: each check spawns a
``lake build``, and concurrent ``lake build`` processes contend on Lake's
global build directory lock, so parallelism there causes lock contention and
timeouts rather than speedup (Lake parallelises dependency builds internally).
These tests stub the actual compile call so they're fast and deterministic,
and cover: full population, the jobs=1 path, empty targets, per-file error
isolation, and the default worker count.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from unittest import mock
from pathlib import Path


_SYNC = Path(__file__).resolve().parent.parent / (
    "src/archon/.archon-src/skills/lean4/lib/scripts/sync_leanok.py"
)


def _load_sync_module():
    name = "sync_leanok_module"
    spec = importlib.util.spec_from_file_location(name, _SYNC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # @dataclass needs the module registered (3.10)
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod


class PopulateCompileCacheTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_sync_module()

    def setUp(self):
        self._orig = self.mod._file_compiles
        self.addCleanup(setattr, self.mod, "_file_compiles", self._orig)

    def test_populates_every_file_correctly(self):
        seen = []
        self.mod._file_compiles = lambda f, p: (seen.append(f) or True)
        files = {Path(f"/x/f{i}.lean") for i in range(8)}

        cache = self.mod._populate_compile_cache(files, Path("/x"), jobs=8)

        # Every target is checked exactly once and recorded.
        self.assertEqual(set(cache), files)
        self.assertEqual(set(seen), files)
        self.assertEqual(len(seen), len(files))
        self.assertTrue(all(cache.values()))

    def test_serial_path_jobs_one(self):
        seen = []
        self.mod._file_compiles = lambda f, p: (seen.append(f) or True)
        files = {Path("/x/a.lean"), Path("/x/b.lean")}
        cache = self.mod._populate_compile_cache(files, Path("/x"), jobs=1)
        self.assertEqual(set(cache), files)
        self.assertEqual(set(seen), files)

    def test_empty_targets(self):
        self.assertEqual(
            self.mod._populate_compile_cache(set(), Path("/x"), jobs=4), {}
        )

    def test_per_file_error_isolated_to_none(self):
        def boom(f, p):
            if f.name == "bad.lean":
                raise RuntimeError("compile blew up")
            return True
        self.mod._file_compiles = boom
        files = {Path("/x/ok.lean"), Path("/x/bad.lean")}
        cache = self.mod._populate_compile_cache(files, Path("/x"), jobs=4)
        self.assertIsNone(cache[Path("/x/bad.lean")])
        self.assertTrue(cache[Path("/x/ok.lean")])

    def test_default_jobs_respects_env(self):
        with mock.patch.dict("os.environ", {"ARCHON_SYNC_LEANOK_JOBS": "3"}):
            self.assertEqual(self.mod._default_jobs(), 3)

    def test_default_jobs_is_positive(self):
        self.assertGreaterEqual(self.mod._default_jobs(), 1)


if __name__ == "__main__":
    unittest.main()
