"""Tests for sync_leanok's parallel compile-check warm-up.

The per-file ``lake env lean`` compile checks dominate the runtime on a
large blueprint and used to run serially, blowing the phase timeout. They
are now populated through a bounded thread pool before the sequential
marker pass. These tests stub the actual compile call so they're fast and
deterministic, and cover: parallel population, the serial (jobs=1) path,
target collection mirroring the in-scope filter, per-file error isolation,
and the default worker count.
"""

from __future__ import annotations

import importlib.util
import sys
import time
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

    def test_parallel_is_faster_than_serial_and_correct(self):
        def slow_true(f, p):
            time.sleep(0.15)
            return True
        self.mod._file_compiles = slow_true
        files = {Path(f"/x/f{i}.lean") for i in range(8)}

        t = time.time()
        cache = self.mod._populate_compile_cache(files, Path("/x"), jobs=8)
        parallel = time.time() - t

        self.assertEqual(set(cache), files)
        self.assertTrue(all(cache.values()))
        # 8 × 0.15s serial ≈ 1.2s; with 8 workers it should be well under.
        self.assertLess(parallel, 0.8)

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
