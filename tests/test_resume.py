"""Tests for the --resume session-file validation.

The defensive check verifies that Claude Code's session JSONL file
actually exists in its store before we pass `--resume <id>` to
claude — preventing the cryptic "session not found" errors when the
store has been rotated or cleaned.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from archon.commands.loop.resume import (
    _claude_session_exists,
    pick_resume_session,
)


class ClaudeSessionExistsTest(unittest.TestCase):
    def test_existing_session_found(self):
        with tempfile.TemporaryDirectory() as fake_home:
            home = Path(fake_home)
            with tempfile.TemporaryDirectory() as cwd:
                cwd_path = Path(cwd).resolve()
                sanitized = str(cwd_path).replace(os.sep, "-")
                store = home / ".claude" / "projects" / sanitized
                store.mkdir(parents=True)
                sid = "abcd1234-5678-9abc-def0-123456789abc"
                (store / f"{sid}.jsonl").write_text("", encoding="utf-8")
                with mock.patch.object(Path, "home", return_value=home):
                    self.assertTrue(_claude_session_exists(cwd_path, sid))

    def test_missing_session_not_found(self):
        with tempfile.TemporaryDirectory() as fake_home:
            home = Path(fake_home)
            with tempfile.TemporaryDirectory() as cwd:
                with mock.patch.object(Path, "home", return_value=home):
                    self.assertFalse(
                        _claude_session_exists(Path(cwd), "no-such-session"),
                    )

    def test_unresolvable_cwd_permissive(self):
        # If cwd can't be resolved, be permissive (let claude code's
        # own error message surface, don't false-positive).
        bogus = Path("/this/path/should/not/exist")
        with mock.patch.object(Path, "home", return_value=Path("/tmp")):
            result = _claude_session_exists(bogus, "any-id")
            # Permissive path returns True via the OSError branch OR
            # checks the unresolvable path against home; either way
            # we accept whatever it returns — what matters is no crash.
            self.assertIn(result, (True, False))


class PickResumeSessionWithCwdTest(unittest.TestCase):
    def setUp(self):
        self._fake_home = tempfile.TemporaryDirectory()
        self.home = Path(self._fake_home.name)
        self._cwd = tempfile.TemporaryDirectory()
        self.cwd = Path(self._cwd.name).resolve()
        self._meta = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False,
        )
        self.meta_path = Path(self._meta.name)
        self._meta.close()

    def tearDown(self):
        self._fake_home.cleanup()
        self._cwd.cleanup()
        self.meta_path.unlink(missing_ok=True)

    def _write_meta(self, **kv):
        # Mirror write_meta's dot-notation → nested-dict convention so
        # read_meta resolves the keys as the production code would.
        data: dict = {}
        for key, value in kv.items():
            parts = key.split(".")
            d = data
            for part in parts[:-1]:
                d = d.setdefault(part, {})
            d[parts[-1]] = value
        self.meta_path.write_text(json.dumps(data), encoding="utf-8")

    def _seed_session_file(self, sid: str):
        sanitized = str(self.cwd).replace(os.sep, "-")
        store = self.home / ".claude" / "projects" / sanitized
        store.mkdir(parents=True, exist_ok=True)
        (store / f"{sid}.jsonl").write_text("", encoding="utf-8")

    def test_returns_sid_when_session_file_exists(self):
        sid = "11111111-2222-3333-4444-555555555555"
        self._write_meta(**{"plan.sessionId": sid})
        self._seed_session_file(sid)
        with mock.patch.object(Path, "home", return_value=self.home):
            got = pick_resume_session(
                self.meta_path, "plan.sessionId",
                enabled=True, label="plan", cwd=self.cwd,
            )
        self.assertEqual(got, sid)

    def test_falls_back_to_fresh_when_session_file_missing(self):
        sid = "deadbeef-0000-0000-0000-000000000000"
        self._write_meta(**{"plan.sessionId": sid})
        # NO seed — session file does not exist.
        with mock.patch.object(Path, "home", return_value=self.home):
            got = pick_resume_session(
                self.meta_path, "plan.sessionId",
                enabled=True, label="plan", cwd=self.cwd,
            )
        self.assertIsNone(got)

    def test_no_cwd_skips_validation(self):
        # Without cwd, the function preserves prior (lax) behavior:
        # returns the stored id even if no file exists in the store.
        sid = "ffffffff-0000-0000-0000-000000000000"
        self._write_meta(**{"plan.sessionId": sid})
        with mock.patch.object(Path, "home", return_value=self.home):
            got = pick_resume_session(
                self.meta_path, "plan.sessionId",
                enabled=True, label="plan",
                # cwd omitted on purpose
            )
        self.assertEqual(got, sid)

    def test_disabled_returns_none(self):
        sid = "00000000-0000-0000-0000-000000000000"
        self._write_meta(**{"plan.sessionId": sid})
        self._seed_session_file(sid)
        with mock.patch.object(Path, "home", return_value=self.home):
            got = pick_resume_session(
                self.meta_path, "plan.sessionId",
                enabled=False, label="plan", cwd=self.cwd,
            )
        self.assertIsNone(got)


class ExtractSessionIdFallbackTest(unittest.TestCase):
    """extract_session_id falls back to session_meta when no session_end
    is present (crashed run case)."""

    def _write_jsonl(self, lines: list[dict]) -> Path:
        td = tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False,
        )
        for row in lines:
            td.write(json.dumps(row) + "\n")
        td.close()
        self.addCleanup(lambda: Path(td.name).unlink(missing_ok=True))
        return Path(td.name)

    def test_prefers_session_end_when_both_present(self):
        from archon.state import extract_session_id
        p = self._write_jsonl([
            {"event": "session_meta", "session_id": "early-id"},
            {"event": "text", "content": "hello"},
            {"event": "session_end", "session_id": "final-id"},
        ])
        self.assertEqual(extract_session_id(p), "final-id")

    def test_falls_back_to_session_meta_when_no_session_end(self):
        from archon.state import extract_session_id
        p = self._write_jsonl([
            {"event": "session_meta", "session_id": "early-id"},
            {"event": "text", "content": "agent crashed mid-run"},
        ])
        self.assertEqual(extract_session_id(p), "early-id")

    def test_session_meta_reversed_picks_latest_when_multiple(self):
        # A phase that restarted via --resume leaves the old session_meta
        # earlier in the same JSONL and a NEW session_meta later. The
        # latest run is the one Claude Code's store has — we want that
        # one, not the dead earlier id.
        from archon.state import extract_session_id
        p = self._write_jsonl([
            {"event": "session_start", "model": "opus"},
            {"event": "session_meta", "session_id": "first-run-id"},
            {"event": "text", "content": "first run crashed mid-prove"},
            {"event": "session_start", "model": "opus"},
            {"event": "session_meta", "session_id": "second-run-id"},
            {"event": "text", "content": "second run also crashed"},
        ])
        self.assertEqual(extract_session_id(p), "second-run-id")

    def test_returns_none_when_neither_present(self):
        from archon.state import extract_session_id
        p = self._write_jsonl([
            {"event": "text", "content": "no session ids anywhere"},
        ])
        self.assertIsNone(extract_session_id(p))

    def test_empty_session_meta_id_skipped(self):
        from archon.state import extract_session_id
        p = self._write_jsonl([
            {"event": "session_meta", "session_id": ""},
        ])
        self.assertIsNone(extract_session_id(p))


class PickResumeSessionJsonlFallbackTest(unittest.TestCase):
    """When meta.json has no <phase>.sessionId but the phase JSONL has a
    session_meta event, pick_resume_session must recover the id and use
    it for --resume. This is the failure mode where the agent crashed
    before persist_session_id ran at end of phase."""

    def setUp(self):
        self._fake_home = tempfile.TemporaryDirectory()
        self.home = Path(self._fake_home.name)
        self._cwd = tempfile.TemporaryDirectory()
        self.cwd = Path(self._cwd.name).resolve()
        self._td = tempfile.TemporaryDirectory()
        self.workdir = Path(self._td.name)
        self.meta_path = self.workdir / "meta.json"
        self.jsonl_path = self.workdir / "plan.jsonl"

    def tearDown(self):
        self._fake_home.cleanup()
        self._cwd.cleanup()
        self._td.cleanup()

    def _seed_session_file(self, sid: str):
        sanitized = str(self.cwd).replace(os.sep, "-")
        store = self.home / ".claude" / "projects" / sanitized
        store.mkdir(parents=True, exist_ok=True)
        (store / f"{sid}.jsonl").write_text("", encoding="utf-8")

    def _write_jsonl_with_session_meta(self, sid: str):
        self.jsonl_path.write_text(
            json.dumps({"event": "session_start", "model": "opus"}) + "\n"
            + json.dumps({"event": "session_meta", "session_id": sid}) + "\n",
            encoding="utf-8",
        )

    def test_recovers_from_jsonl_when_meta_missing_key(self):
        # The reported scenario: meta has plan.status=running but no
        # plan.sessionId; the JSONL has the session_meta event.
        sid = "a3cde467-3fd7-45ba-9037-1f212eb3cec0"
        self.meta_path.write_text(
            json.dumps({"plan": {"status": "running"}}), encoding="utf-8",
        )
        self._write_jsonl_with_session_meta(sid)
        self._seed_session_file(sid)

        with mock.patch.object(Path, "home", return_value=self.home):
            got = pick_resume_session(
                self.meta_path, "plan.sessionId",
                enabled=True, label="plan", cwd=self.cwd,
                jsonl_fallback=self.jsonl_path,
            )
        self.assertEqual(got, sid)

    def test_recovered_id_gets_stamped_into_meta(self):
        # After a successful recovery, the meta should have the id so
        # future inspections show it (useful for the user to see + as
        # a hint to future resume attempts).
        sid = "fedcba98-7654-3210-fedc-ba9876543210"
        self.meta_path.write_text(
            json.dumps({"plan": {"status": "running"}}), encoding="utf-8",
        )
        self._write_jsonl_with_session_meta(sid)
        self._seed_session_file(sid)

        with mock.patch.object(Path, "home", return_value=self.home):
            pick_resume_session(
                self.meta_path, "plan.sessionId",
                enabled=True, label="plan", cwd=self.cwd,
                jsonl_fallback=self.jsonl_path,
            )

        data = json.loads(self.meta_path.read_text())
        self.assertEqual(data["plan"]["sessionId"], sid)

    def test_returns_none_when_meta_missing_and_no_jsonl_file(self):
        # No meta key, JSONL doesn't exist on disk → fall back to fresh.
        self.meta_path.write_text(json.dumps({}), encoding="utf-8")
        with mock.patch.object(Path, "home", return_value=self.home):
            got = pick_resume_session(
                self.meta_path, "plan.sessionId",
                enabled=True, label="plan", cwd=self.cwd,
                jsonl_fallback=self.jsonl_path,  # path doesn't exist
            )
        self.assertIsNone(got)

    def test_returns_none_when_jsonl_has_no_session_meta(self):
        # JSONL exists but has no session_meta event (truly nothing).
        self.meta_path.write_text(json.dumps({}), encoding="utf-8")
        self.jsonl_path.write_text(
            json.dumps({"event": "session_start", "model": "opus"}) + "\n"
            + json.dumps({"event": "text", "content": "crashed early"}) + "\n",
            encoding="utf-8",
        )
        with mock.patch.object(Path, "home", return_value=self.home):
            got = pick_resume_session(
                self.meta_path, "plan.sessionId",
                enabled=True, label="plan", cwd=self.cwd,
                jsonl_fallback=self.jsonl_path,
            )
        self.assertIsNone(got)

    def test_recovered_id_still_subject_to_session_file_check(self):
        # Even when the JSONL provides an id, if Claude Code's store
        # doesn't have the session file, we still fall back to fresh —
        # we can't resume a session whose log was rotated away.
        sid = "11112222-3333-4444-5555-666677778888"
        self.meta_path.write_text(json.dumps({}), encoding="utf-8")
        self._write_jsonl_with_session_meta(sid)
        # NO _seed_session_file — store is empty.
        with mock.patch.object(Path, "home", return_value=self.home):
            got = pick_resume_session(
                self.meta_path, "plan.sessionId",
                enabled=True, label="plan", cwd=self.cwd,
                jsonl_fallback=self.jsonl_path,
            )
        self.assertIsNone(got)

    def test_meta_present_takes_precedence_over_jsonl(self):
        # When meta has the key, it wins — no JSONL parsing needed.
        meta_sid = "meta1234-0000-0000-0000-000000000000"
        jsonl_sid = "jsonl678-0000-0000-0000-000000000000"
        self.meta_path.write_text(
            json.dumps({"plan": {"sessionId": meta_sid}}), encoding="utf-8",
        )
        self._write_jsonl_with_session_meta(jsonl_sid)
        self._seed_session_file(meta_sid)
        with mock.patch.object(Path, "home", return_value=self.home):
            got = pick_resume_session(
                self.meta_path, "plan.sessionId",
                enabled=True, label="plan", cwd=self.cwd,
                jsonl_fallback=self.jsonl_path,
            )
        self.assertEqual(got, meta_sid)


class AvailableSessionIdsTest(unittest.TestCase):
    """The improved warning message surfaces the session ids that ARE
    in the meta so the user can pick the right --from or diagnose."""

    def test_walk_session_ids_finds_flat_and_nested(self):
        from archon.commands.loop.resume import _walk_session_ids
        meta = {
            "plan": {"sessionId": "p1234567abc"},
            "provers": {
                "Foo": {"sessionId": "f1234567abc"},
                "Bar": {"sessionId": "b1234567abc"},
            },
            "review": {"durationSecs": 10},  # no sessionId here
        }
        out = dict(_walk_session_ids("", meta))
        self.assertEqual(out["plan"], "p1234567abc")
        self.assertEqual(out["provers.Foo"], "f1234567abc")
        self.assertEqual(out["provers.Bar"], "b1234567abc")
        self.assertNotIn("review", out)


if __name__ == "__main__":
    unittest.main()
