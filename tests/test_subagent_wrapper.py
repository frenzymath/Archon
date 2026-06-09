"""Tests for the subagent dispatch wrapper's archon-CLI resolution.

The wrapper (``src/archon/.archon-src/tools/subagent_wrapper.py``, installed
into projects as ``.claude/tools/archon-subagent.py``) shells out to the
``archon`` CLI to dispatch a subagent. It must locate that CLI even when the
console script isn't on PATH — the common case inside the Codex sandbox — by
falling back to ``<python> -m archon``.
"""

from __future__ import annotations

import importlib.util
import os
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
        with patch.dict(os.environ, {}, clear=False) as _env, \
             patch.object(self.w.shutil, "which", return_value=None), \
             patch.object(self.w.importlib.util, "find_spec", return_value=None):
            os.environ.pop("ARCHON_CLI_BIN", None)
            os.environ.pop("ARCHON_PYTHON", None)
            self.assertIsNone(self.w._resolve_archon_cmd())

    # ── codex-sandbox path: PATH is reset, env vars survive ───────────────

    def test_stamped_cli_bin_wins_over_path(self):
        # Inside the codex login shell PATH lacks the venv bin/, but the
        # ARCHON_CLI_BIN stamp survives and must take precedence.
        with patch.dict(os.environ, {"ARCHON_CLI_BIN": "/venv/bin/archon"}), \
             patch.object(self.w.os.path, "isfile", return_value=True), \
             patch.object(self.w.shutil, "which", return_value="/some/other/archon"):
            self.assertEqual(self.w._resolve_archon_cmd(), ["/venv/bin/archon"])

    def test_stamped_cli_bin_ignored_when_missing_file(self):
        # A stale stamp pointing at a now-absent file must not be trusted;
        # fall through to the next resolution step.
        with patch.dict(os.environ, {"ARCHON_CLI_BIN": "/gone/archon"}), \
             patch.object(self.w.os.path, "isfile", return_value=False), \
             patch.object(self.w.shutil, "which", return_value="/usr/bin/archon"):
            self.assertEqual(self.w._resolve_archon_cmd(), ["/usr/bin/archon"])

    def test_falls_back_to_stamped_python_when_no_cli_and_no_path(self):
        with patch.dict(os.environ, {"ARCHON_PYTHON": "/venv/bin/python"}), \
             patch.object(self.w.os.path, "isfile", return_value=True), \
             patch.object(self.w.shutil, "which", return_value=None):
            os.environ.pop("ARCHON_CLI_BIN", None)
            self.assertEqual(
                self.w._resolve_archon_cmd(), ["/venv/bin/python", "-m", "archon"]
            )

    def test_path_console_script_preferred_over_stamped_python(self):
        with patch.dict(
            os.environ,
            {"ARCHON_PYTHON": "/venv/bin/python"},
        ), patch.object(self.w.os.path, "isfile", return_value=True), \
             patch.object(self.w.shutil, "which", return_value="/usr/bin/archon"):
            os.environ.pop("ARCHON_CLI_BIN", None)
            self.assertEqual(self.w._resolve_archon_cmd(), ["/usr/bin/archon"])


if __name__ == "__main__":
    unittest.main()
