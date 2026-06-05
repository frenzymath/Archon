"""Tests that every archon agent is launched with the native sub-agent /
wakeup tools disallowed.

Why this matters: archon's orchestrator runs each phase agent as a
one-shot headless ``claude -p`` and advances to the next iteration the
moment the subprocess exits. Subagents must be dispatched via the
*blocking* ``archon-subagent.py`` Bash CLI so the dispatching agent's
turn stays alive until the work finishes. If an agent instead uses the
native ``Agent`` tool + ``ScheduleWakeup``, it ends its turn
mid-dispatch, the headless process exits, and the orchestrator launches
the next iteration over still-in-flight subagents. Disallowing those
tools at the CLI level is what prevents that race — these tests lock the
flag in.
"""

from __future__ import annotations

import tempfile
import unittest

from archon.agent import (
    DISALLOWED_NATIVE_TOOLS,
    ClaudeAgent,
    ClaudeBackend,
    ClaudePBackend,
)


def _disallowed_in(cmd: list[str]) -> list[str]:
    """Return the tool names passed after ``--disallowedTools`` in cmd."""
    assert "--disallowedTools" in cmd, f"--disallowedTools absent from {cmd}"
    idx = cmd.index("--disallowedTools")
    # The flag is variadic (space-separated bare names) and, in these
    # builds, is the last group appended by _build_flags, so everything
    # after it up to the next ``--flag`` belongs to it.
    names: list[str] = []
    for tok in cmd[idx + 1:]:
        if tok.startswith("--"):
            break
        names.append(tok)
    return names


class DisallowedToolsTest(unittest.TestCase):
    def test_constant_blocks_spawn_and_wakeup(self) -> None:
        # Agent (native sub-agent spawn) and ScheduleWakeup are the two
        # tools that let an agent end its turn mid-dispatch. Task is the
        # defensive alias for CLI builds that name the spawner Task.
        self.assertIn("Agent", DISALLOWED_NATIVE_TOOLS)
        self.assertIn("ScheduleWakeup", DISALLOWED_NATIVE_TOOLS)
        self.assertIn("Task", DISALLOWED_NATIVE_TOOLS)

    def test_build_flags_includes_disallowed(self) -> None:
        flags = ClaudeAgent(model="opus")._build_flags("opus")
        self.assertEqual(
            _disallowed_in(flags), list(DISALLOWED_NATIVE_TOOLS),
        )

    def test_build_flags_keeps_benign_tools_available(self) -> None:
        # We only ever disallow the native spawn/wakeup tools — never the
        # workhorse tools archon agents actually need.
        disallowed = set(_disallowed_in(ClaudeAgent()._build_flags("opus")))
        for keep in ("Bash", "Read", "Write", "Edit", "Grep", "Glob"):
            self.assertNotIn(keep, disallowed)

    def test_headless_cmd_carries_disallowed_default_backend(self) -> None:
        agent = ClaudeAgent(model="opus")
        cmd, _ = ClaudeBackend().build_headless(
            "hi",
            model="opus",
            flags=agent._build_flags("opus"),
            resume_session_id=None,
            base_env={},
        )
        self.assertEqual(
            _disallowed_in(cmd), list(DISALLOWED_NATIVE_TOOLS),
        )

    def test_headless_cmd_carries_disallowed_claude_p_backend(self) -> None:
        # The claude-p backend forwards the same flags; the guard must
        # survive that path too. Pin config_dir to a temp dir so the
        # backend's settings-merge can't touch the real ~/.claude.
        agent = ClaudeAgent(model="opus")
        with tempfile.TemporaryDirectory() as tmp:
            cmd, _ = ClaudePBackend(config_dir=tmp).build_headless(
                "hi",
                model="opus",
                flags=agent._build_flags("opus"),
                resume_session_id=None,
                base_env={},
            )
        self.assertEqual(
            _disallowed_in(cmd), list(DISALLOWED_NATIVE_TOOLS),
        )


if __name__ == "__main__":
    unittest.main()
