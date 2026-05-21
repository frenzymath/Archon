"""Regression tests for ``_read_config_file`` argument validation.

``find_multilane_config(state_dir)`` returns ``None`` when the
pre-v0.2.0 ``.archon/multilane/`` layout isn't there, but
``load_multilane_config`` stayed callable with arbitrary paths.
A caller passing the state dir itself crashed with a confusing
``IsADirectoryError`` from inside ``read_text``. Surface a clear
error instead, pointing at the v0.2.0 ``config.json`` flow.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from archon.multilane.config import load_multilane_config


class LoadMultilaneConfigGuardsTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(_rmtree, self._tmp)
        self.root = Path(self._tmp)

    def test_directory_raises_clear_error(self):
        # Pre-v0.2.0 callers handed in ``state_dir`` itself.
        with self.assertRaises(ValueError) as cm:
            load_multilane_config(self.root)
        msg = str(cm.exception)
        self.assertIn("directory", msg)
        # Mentions the migration path so the user knows what to do.
        self.assertIn("config.json", msg)

    def test_missing_file_raises_clear_error(self):
        with self.assertRaises(FileNotFoundError) as cm:
            load_multilane_config(self.root / "nope.json")
        self.assertIn("Multilane config not found", str(cm.exception))

    def test_valid_json_loads(self):
        # Sanity: the guard didn't break the happy path.
        cfg_path = self.root / "lanes.json"
        cfg_path.write_text(
            '{"enabled": true, "version": 1, "base_ref": "main", '
            '"grace_minutes": 5, "lanes": []}',
            encoding="utf-8",
        )
        config = load_multilane_config(cfg_path)
        self.assertTrue(config.enabled)
        self.assertEqual(config.version, 1)


def _rmtree(path: str) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
