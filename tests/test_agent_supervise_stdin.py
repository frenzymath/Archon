"""Regression test: the supervised agent subprocess must get ``stdin=DEVNULL``.

``codex exec [PROMPT]`` treats a piped (non-TTY) stdin as "read additional
prompt and append it", so it blocks on "Reading additional input from
stdin..." until EOF. A supervised, headless run never feeds stdin, so
:func:`archon.agent.supervise_streamed_run` must hand the child an
already-closed stdin (``DEVNULL``) — otherwise the child inherits the
parent's open stdin pipe, hangs, and the idle-watchdog kills it (the codex
subagent then reports "failed (0s)" / idle). This guards that wiring for
both the ``parser_cmd`` and ``stdout_dest`` spawn branches.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import archon.agent as agentmod
from archon.agent import supervise_streamed_run


# A trivial "agent": read stdin to EOF (would block on an open inherited
# pipe), record how many bytes it saw, emit one JSONL row so the watchdog
# registers liveness, then exit 0.
def _agent_script(jsonl: Path, marker: Path) -> str:
    return (
        "import sys\n"
        "data = sys.stdin.read()\n"
        f"open({str(marker)!r}, 'w').write(str(len(data)))\n"
        f"open({str(jsonl)!r}, 'a').write('{{\"event\": \"text\"}}\\n')\n"
        "sys.exit(0)\n"
    )


class SuperviseStdinTest(unittest.TestCase):
    def _run(self, *, parser: bool):
        recorded: list[dict] = []
        real_popen = subprocess.Popen

        def spy(cmd, **kw):
            recorded.append(kw)
            return real_popen(cmd, **kw)

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            jsonl = tmp / "out.jsonl"
            marker = tmp / "stdin_len.txt"
            stderr = tmp / "stderr.log"
            agent_cmd = [sys.executable, "-c", _agent_script(jsonl, marker)]

            kwargs: dict = dict(
                cwd=tmp, env=None, jsonl_path=jsonl,
                stderr_dest=str(stderr), idle_timeout_s=15,
            )
            if parser:
                # Drain the agent's stdout into a no-op parser.
                kwargs["parser_cmd"] = [sys.executable, "-c", "import sys;[None for _ in sys.stdin]"]
            else:
                kwargs["stdout_dest"] = str(tmp / "stdout.log")

            with mock.patch.object(agentmod.subprocess, "Popen", spy):
                result = supervise_streamed_run(agent_cmd, **kwargs)

            # Read the marker BEFORE the tempdir is cleaned up on block exit.
            stdin_len = marker.read_text() if marker.exists() else None
            return result, recorded, stdin_len

    def test_stdout_dest_branch_passes_devnull_and_completes(self):
        result, recorded, stdin_len = self._run(parser=False)
        # Exactly one spawn (the agent) in stdout_dest mode.
        self.assertEqual(len(recorded), 1)
        self.assertIs(recorded[0].get("stdin"), subprocess.DEVNULL)
        # It completed (EOF on stdin → no hang → not idle-killed).
        self.assertFalse(result.idle_timeout_hit)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(stdin_len, "0")  # stdin was empty (EOF)

    def test_parser_branch_agent_gets_devnull_stdin(self):
        result, recorded, stdin_len = self._run(parser=True)
        # The agent proc is the one whose stdout is PIPE'd to the parser.
        agent_kwargs = [k for k in recorded if k.get("stdout") is subprocess.PIPE]
        self.assertEqual(len(agent_kwargs), 1)
        self.assertIs(agent_kwargs[0].get("stdin"), subprocess.DEVNULL)
        self.assertFalse(result.idle_timeout_hit)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
