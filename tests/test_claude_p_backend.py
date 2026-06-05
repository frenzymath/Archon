"""Tests for ``ClaudePBackend`` flag assembly.

The maintained claude-p fork (github.com/AxelDlv00/claude-p) adds
``--trust-workspace`` to dismiss the TUI's "do you trust this folder?"
prompt non-interactively. Archon feature-detects optional flags from
``claude-p --help`` so it never passes one a stock-upstream build would
reject (argparse aborts the whole run). These tests pin that behaviour
without depending on which claude-p (if any) is installed.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from archon import agent
from archon.agent import ClaudePBackend


def _build(**probe):
    """Build a headless claude-p cmd with a stubbed --help capability probe.

    ``probe`` maps a flag → bool. The patched ``_claude_p_supports`` reads it.
    """
    with patch.object(agent, "_claude_p_supports", side_effect=lambda f: probe.get(f, False)):
        cmd, _env = ClaudePBackend().build_headless(
            "PROMPT", model="opus", flags=["--model", "opus"],
            resume_session_id=None, base_env={},
        )
    return cmd


class ClaudePBackendFlagsTest(unittest.TestCase):
    def test_trust_workspace_added_when_supported(self):
        cmd = _build(**{"--trust-workspace": True})
        self.assertIn("--trust-workspace", cmd)

    def test_session_events_preferred_over_deltas(self):
        # When the richer flag is available, prefer it and DON'T also pass
        # the lower-fidelity terminal-scrape flag.
        cmd = _build(**{"--stream-session-events": True, "--live-tui-deltas": True})
        self.assertIn("--stream-session-events", cmd)
        self.assertNotIn("--live-tui-deltas", cmd)

    def test_falls_back_to_deltas_when_no_session_events(self):
        cmd = _build(**{"--stream-session-events": False, "--live-tui-deltas": True})
        self.assertNotIn("--stream-session-events", cmd)
        self.assertIn("--live-tui-deltas", cmd)

    def test_unsupported_flags_omitted(self):
        # Stock-upstream claude-p without any of the fork flags: none are
        # passed, so argparse can't abort on an unknown flag.
        cmd = _build()
        self.assertNotIn("--trust-workspace", cmd)
        self.assertNotIn("--live-tui-deltas", cmd)
        self.assertNotIn("--stream-session-events", cmd)

    def test_always_present_flags(self):
        cmd = _build()
        self.assertEqual(cmd[0], "claude-p")
        self.assertIn("--timeout-sec", cmd)
        self.assertIn("--quiet-after-sec", cmd)
        self.assertIn("PROMPT", cmd)

    def test_probe_returns_empty_when_binary_missing(self):
        # _claude_p_help must degrade to "" (→ no flags) if claude-p errors.
        agent._claude_p_help.cache_clear()
        with patch.object(agent.subprocess, "run", side_effect=OSError):
            self.assertEqual(agent._claude_p_help(), "")
            self.assertFalse(agent._claude_p_supports("--trust-workspace"))
        agent._claude_p_help.cache_clear()


if __name__ == "__main__":
    unittest.main()
