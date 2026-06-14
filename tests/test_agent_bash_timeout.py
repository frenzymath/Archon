"""Tests that archon raises the spawned agent's foreground-Bash timeout.

Claude Code defaults a Bash command to a 2-min timeout (10-min ceiling)
and auto-backgrounds a foreground command that overruns it. Archon
dispatches subagents as *blocking* Bash calls whose child claude session
runs 10-15+ min; if that call auto-backgrounds, the dispatching agent's
turn ends and the orchestrator advances over the still-running subagent.
Raising BASH_DEFAULT_TIMEOUT_MS / BASH_MAX_TIMEOUT_MS in the agent env
keeps the dispatch in the foreground. These tests lock that in.
"""

from __future__ import annotations

import unittest
from unittest import mock

from archon.agent import BASH_FOREGROUND_TIMEOUT_MS, ClaudeAgent


class BashTimeoutEnvTest(unittest.TestCase):
    def test_default_raises_both_timeout_vars(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            env = ClaudeAgent()._build_env(None)
        self.assertEqual(
            env["BASH_DEFAULT_TIMEOUT_MS"], str(BASH_FOREGROUND_TIMEOUT_MS),
        )
        self.assertEqual(
            env["BASH_MAX_TIMEOUT_MS"], str(BASH_FOREGROUND_TIMEOUT_MS),
        )

    def test_default_is_well_above_a_long_walker(self) -> None:
        # A cone walker can run ~11 min; the ceiling must clear it with
        # margin so the blocking dispatch never auto-backgrounds.
        self.assertGreaterEqual(BASH_FOREGROUND_TIMEOUT_MS, 20 * 60 * 1000)

    def test_explicit_env_override_wins(self) -> None:
        with mock.patch.dict(
            "os.environ", {"BASH_MAX_TIMEOUT_MS": "12345"}, clear=True,
        ):
            env = ClaudeAgent()._build_env(None)
        self.assertEqual(env["BASH_MAX_TIMEOUT_MS"], "12345")

    def test_caller_override_wins(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            env = ClaudeAgent()._build_env({"BASH_DEFAULT_TIMEOUT_MS": "999"})
        self.assertEqual(env["BASH_DEFAULT_TIMEOUT_MS"], "999")


if __name__ == "__main__":
    unittest.main()
