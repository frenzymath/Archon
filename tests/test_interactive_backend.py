"""Tests for the `interactive` claude backend.

The interactive backend launches claude in the foreground for a human to
drive (the stable subscription fallback). ClaudeAgent.run routes to a
foreground path instead of the headless stream-json pipeline, and the loop
forces serial execution. These tests cover the resolver, the build_runner
threading, and ClaudeAgent.run's interactive routing (mocking the actual
foreground claude launch).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from archon.agent import (
    ClaudeAgent,
    InteractiveBackend,
    build_runner,
)
from archon.commands.tooling.project_config import (
    HarnessDescriptor,
    ProjectConfig,
    resolve_claude_backend,
)


class ResolveInteractiveTest(unittest.TestCase):
    def test_cli_value_resolves(self):
        b = resolve_claude_backend(ProjectConfig(), cli_value="interactive")
        self.assertIsInstance(b, InteractiveBackend)

    def test_config_value_resolves(self):
        cfg = ProjectConfig(raw={"loop": {"claude_backend": "interactive"}})
        self.assertIsInstance(resolve_claude_backend(cfg), InteractiveBackend)

    def test_build_runner_threads_interactive_backend(self):
        b = InteractiveBackend()
        r = build_runner(role="prover", model="opus", backend=b)
        self.assertIsInstance(r, ClaudeAgent)
        self.assertIs(r.backend, b)

    def test_descriptor_backend_override_to_interactive(self):
        d = HarnessDescriptor(name="cc-i", runner="claude-code", backend="interactive")
        r = build_runner(role="prover", descriptor=d)
        self.assertIsInstance(r.backend, InteractiveBackend)

    def test_build_headless_raises(self):
        with self.assertRaises(NotImplementedError):
            InteractiveBackend().build_headless(
                "p", model="opus", flags=[], resume_session_id=None, base_env={},
            )


class InteractiveRunRoutingTest(unittest.TestCase):
    """ClaudeAgent.run must route to the foreground path for this backend,
    skipping the headless stream pipeline, and stamp a minimal JSONL."""

    def _run(self, exit_code: int):
        agent = ClaudeAgent(model="opus", role="prover", backend=InteractiveBackend())
        with tempfile.TemporaryDirectory() as d:
            log_base = Path(d) / "prover"
            with patch.object(
                ClaudeAgent, "run_interactive", return_value=exit_code,
            ) as m:
                ok = agent.run("PROVE THIS", cwd=Path(d), log_base=log_base)
            rows = [
                json.loads(line)
                for line in (Path(d) / "prover.jsonl").read_text().splitlines()
                if line.strip()
            ]
            return ok, rows, m

    def test_clean_exit_is_success_and_stamps_jsonl(self):
        ok, rows, mock_ri = self._run(0)
        self.assertTrue(ok)
        # Foreground path was used (not the headless pipeline).
        mock_ri.assert_called_once()
        events = [r["event"] for r in rows]
        self.assertEqual(events[0], "session_start")
        self.assertIn("prompt", events)
        end = [r for r in rows if r["event"] == "session_end"][0]
        self.assertTrue(end["interactive"])
        self.assertTrue(end["ok"])
        self.assertEqual(end["total_cost_usd"], 0)

    def test_nonzero_exit_is_failure(self):
        ok, rows, _ = self._run(1)
        self.assertFalse(ok)
        end = [r for r in rows if r["event"] == "session_end"][0]
        self.assertFalse(end["ok"])

    def test_no_log_base_still_runs(self):
        agent = ClaudeAgent(model="opus", role="prover", backend=InteractiveBackend())
        with patch.object(ClaudeAgent, "run_interactive", return_value=0):
            self.assertTrue(agent.run("P", cwd=Path(".")))


if __name__ == "__main__":
    unittest.main()
