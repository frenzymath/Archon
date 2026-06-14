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

import os
import time

from archon.agent import (
    ClaudeAgent,
    InteractiveBackend,
    _LiveClaudeSessionTailer,
    _find_claude_session_file,
    _import_claude_session_jsonl,
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


class InteractivePhaseEndToEndTest(unittest.TestCase):
    """The full interactive path: pin --session-id, tail the transcript under
    a custom CLAUDE_CONFIG_DIR, and land converted events in the phase log.
    Reproduces the real bug (custom config dir → no log)."""

    def test_session_under_custom_config_dir_is_captured(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "claude-sophie"
            proj = (cfg / "projects" / "-x")
            proj.mkdir(parents=True)
            log_base = Path(d) / "plan"
            captured = {}

            def fake_run_interactive(self, prompt, *, cwd, extra_args=None, session_id=None):
                # Simulate Claude writing its transcript under CLAUDE_CONFIG_DIR
                # to <projects>/<sanitized>/<session_id>.jsonl.
                captured["sid"] = session_id
                asst = {"type": "assistant", "message": {"content": [
                    {"type": "text", "text": "hi"},
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
                ]}}
                (proj / f"{session_id}.jsonl").write_text(json.dumps(asst) + "\n")
                return 0

            agent = ClaudeAgent(model="opus", role="plan", backend=InteractiveBackend())
            with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(cfg)}), \
                 patch.object(ClaudeAgent, "run_interactive", fake_run_interactive):
                ok = agent.run("PLAN", cwd=Path(d), log_base=log_base)

            self.assertTrue(ok)
            self.assertIsNotNone(captured["sid"])  # a session id was pinned
            rows = [json.loads(l) for l in (Path(d) / "plan.jsonl").read_text().splitlines()]
            by_event = {r["event"]: r for r in rows}
            # session_meta carries the pinned id (so --resume works).
            self.assertEqual(by_event["session_meta"]["session_id"], captured["sid"])
            # The transcript events were captured from the custom config dir.
            self.assertEqual(by_event["text"]["content"], "hi")
            self.assertEqual(by_event["tool_call"]["tool"], "Bash")


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


# A minimal slice of Claude Code's native session-store schema.
_CLAUDE_SESSION = [
    {"type": "summary", "summary": "ignored meta line"},
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "text", "text": "drive this"}]}, "timestamp": "2026-06-08T00:00:00Z"},
    {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "let me think", "signature": "x"},
        {"type": "text", "text": "I'll build it"},
        {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "lake build"}},
    ]}, "timestamp": "2026-06-08T00:00:01Z"},
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1",
         "content": [{"type": "text", "text": "Build succeeded"}]},
    ]}, "timestamp": "2026-06-08T00:00:02Z"},
    {"type": "permission-mode", "mode": "bypassPermissions"},  # ignored
]


class ImportClaudeSessionTest(unittest.TestCase):
    """The interactive backend imports Claude Code's own transcript into the
    phase JSONL so the dashboard shows the real conversation."""

    def _write_session(self, path: Path):
        path.write_text(
            "\n".join(json.dumps(o) for o in _CLAUDE_SESSION) + "\n",
            encoding="utf-8",
        )

    def test_converts_session_to_archon_events(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "sess.jsonl"
            self._write_session(src)
            dst = str(Path(d) / "plan.jsonl")
            n = _import_claude_session_jsonl(src, dst)
            self.assertEqual(n, 4)  # thinking, text, tool_call, tool_result
            rows = [json.loads(l) for l in Path(dst).read_text().splitlines()]
            by_event = {r["event"]: r for r in rows}
            self.assertEqual(by_event["thinking"]["content"], "let me think")
            self.assertEqual(by_event["text"]["content"], "I'll build it")
            self.assertEqual(by_event["tool_call"]["tool"], "Bash")
            self.assertEqual(by_event["tool_call"]["input"], {"command": "lake build"})
            self.assertEqual(by_event["tool_result"]["content"], "Build succeeded")

    def test_malformed_lines_are_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "sess.jsonl"
            src.write_text('not json\n{"type":"assistant","message":{"content":'
                           '[{"type":"text","text":"ok"}]}}\n', encoding="utf-8")
            dst = str(Path(d) / "plan.jsonl")
            self.assertEqual(_import_claude_session_jsonl(src, dst), 1)

    def test_find_by_session_id_globs_config_dir(self):
        with tempfile.TemporaryDirectory() as home:
            sid = "abc123-session"
            # Session lives under a sanitized project subdir of projects/.
            sdir = Path(home) / ".claude" / "projects" / "-home-proj"
            sdir.mkdir(parents=True)
            target = sdir / f"{sid}.jsonl"
            target.write_text("{}\n")
            (sdir / "other.jsonl").write_text("{}\n")  # different id, ignored
            # Clear CLAUDE_CONFIG_DIR so the default ~/.claude path is exercised.
            with patch.dict(os.environ, {}, clear=True), \
                 patch("archon.agent.Path.home", return_value=Path(home)):
                found = _find_claude_session_file(sid)
            self.assertEqual(found, target)

    def test_find_honors_claude_config_dir(self):
        with tempfile.TemporaryDirectory() as cfg:
            sid = "sid-xyz"
            sdir = Path(cfg) / "projects" / "-some-proj"
            sdir.mkdir(parents=True)
            target = sdir / f"{sid}.jsonl"
            target.write_text("{}\n")
            # Not under ~/.claude — only findable via CLAUDE_CONFIG_DIR.
            found = _find_claude_session_file(sid, env={"CLAUDE_CONFIG_DIR": cfg})
            self.assertEqual(found, target)

    def test_find_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as cfg:
            self.assertIsNone(
                _find_claude_session_file("nope", env={"CLAUDE_CONFIG_DIR": cfg})
            )


class LiveTailerTest(unittest.TestCase):
    """The live tailer mirrors claude-p's live-log behaviour for the
    foreground interactive backend: it converts Claude Code's session JSONL
    into archon events AS the file is written, partial-line safe."""

    def _line(self, o):
        return json.dumps(o) + "\n"

    def test_captures_events_live_and_is_partial_line_safe(self):
        with tempfile.TemporaryDirectory() as cfg:
            sid = "live-sid-1"
            sdir = Path(cfg) / "projects" / "-home-proj"
            sdir.mkdir(parents=True)
            dst = str(Path(cfg) / "plan.jsonl")
            env = {"CLAUDE_CONFIG_DIR": cfg}
            asst = {"type": "assistant", "message": {"content": [
                {"type": "thinking", "thinking": "hmm"},
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
            ]}}
            res = {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": [{"type": "text", "text": "done"}]},
            ]}}
            t = _LiveClaudeSessionTailer(sid, dst, env=env)
            t.start()
            try:
                time.sleep(0.4)
                sf = sdir / f"{sid}.jsonl"   # name matches the pinned session id
                sf.write_text(self._line(asst))
                time.sleep(0.6)
                mid = Path(dst).read_text().count("\n")
                # A partial (unterminated) line must NOT emit a row yet.
                with open(sf, "a") as f:
                    f.write(json.dumps(res))
                time.sleep(0.6)
                self.assertEqual(Path(dst).read_text().count("\n"), mid)
                with open(sf, "a") as f:
                    f.write("\n")
                time.sleep(0.6)
            finally:
                total = t.stop()
            events = [json.loads(l)["event"]
                      for l in Path(dst).read_text().splitlines()]
            self.assertGreaterEqual(mid, 2)               # captured live, mid-run
            self.assertEqual(total, 3)                    # no duplication
            self.assertEqual(events, ["thinking", "tool_call", "tool_result"])
            self.assertEqual(t.locked, sdir / f"{sid}.jsonl")


if __name__ == "__main__":
    unittest.main()
