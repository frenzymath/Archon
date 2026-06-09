"""Tests for the subagent dispatch wrapper's archon-CLI resolution.

The wrapper (``src/archon/.archon-src/tools/subagent_wrapper.py``, installed
into projects as ``.claude/tools/archon-subagent.py``) shells out to the
``archon`` CLI to dispatch a subagent. It must locate that CLI even when the
console script isn't on PATH — the common case inside the Codex sandbox — by
falling back to ``<python> -m archon``.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_WRAPPER_PATH = (
    Path(__file__).resolve().parents[1]
    / "src/archon/.archon-src/tools/subagent_wrapper.py"
)


def _load_wrapper():
    spec = importlib.util.spec_from_file_location("subagent_wrapper", _WRAPPER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ResolveArchonCmdTest(unittest.TestCase):
    def setUp(self):
        self.w = _load_wrapper()

    def test_prefers_console_script_on_path(self):
        with patch.object(self.w.shutil, "which", return_value="/opt/venv/bin/archon"):
            self.assertEqual(self.w._resolve_archon_cmd(), ["/opt/venv/bin/archon"])

    def test_falls_back_to_python_m_archon(self):
        with patch.object(self.w.shutil, "which", return_value=None), \
             patch.object(self.w.importlib.util, "find_spec", return_value=object()):
            self.assertEqual(
                self.w._resolve_archon_cmd(), [sys.executable, "-m", "archon"]
            )

    def test_returns_none_when_unavailable(self):
        with patch.object(self.w.shutil, "which", return_value=None), \
             patch.object(self.w.importlib.util, "find_spec", return_value=None):
            self.assertIsNone(self.w._resolve_archon_cmd())


if __name__ == "__main__":
    unittest.main()
