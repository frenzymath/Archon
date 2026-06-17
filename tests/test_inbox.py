"""Tests for the peer inbox (definition-improvement notes) and project name."""

import tempfile
import unittest
from pathlib import Path

from archon.commands.tooling import inbox, project_config


def _make_project(root: Path, name: str) -> Path:
    p = root / name
    (p / ".archon").mkdir(parents=True)
    return p


class ProjectNameTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_defaults_to_folder_name(self):
        p = _make_project(self.root, "my-proj")
        self.assertEqual(project_config.project_name(p), "my-proj")

    def test_config_name_overrides(self):
        p = _make_project(self.root, "my-proj")
        project_config.config_path(p).write_text('{"name": "canonical"}', encoding="utf-8")
        self.assertEqual(project_config.project_name(p), "canonical")

    def test_blank_config_name_falls_back(self):
        p = _make_project(self.root, "my-proj")
        project_config.config_path(p).write_text('{"name": "   "}', encoding="utf-8")
        self.assertEqual(project_config.project_name(p), "my-proj")


class InboxTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.target = _make_project(self.root, "core")

    def tearDown(self):
        self._td.cleanup()

    def test_write_and_load_note(self):
        inbox.write_note(self.target, "picard-3", "pr", "generalize it", "A.foo", "had to re-prove")
        boxes = inbox.load_all(self.target)
        self.assertEqual(len(boxes), 1)
        self.assertEqual(boxes[0].author, "picard-3")
        note = boxes[0].notes[0]
        self.assertEqual(note.type, "pr")
        self.assertEqual(note.lean_name, "A.foo")
        self.assertEqual(note.payload, "generalize it")
        self.assertEqual(note.status, "open")
        self.assertEqual(note.rationale, "had to re-prove")

    def test_one_file_per_author_update_in_place(self):
        inbox.write_note(self.target, "picard-3", "pr", "v1", "A.foo")
        inbox.write_note(self.target, "picard-3", "pr", "v2 better", "A.foo")
        files = list(inbox.inbox_dir(self.target).glob("*.yaml"))
        self.assertEqual(len(files), 1)  # same author → one file
        boxes = inbox.load_all(self.target)
        self.assertEqual(len(boxes[0].notes), 1)  # same decl → updated, not appended
        self.assertEqual(boxes[0].notes[0].payload, "v2 better")

    def test_distinct_decls_append(self):
        inbox.write_note(self.target, "picard-3", "pr", "x", "A.foo")
        inbox.write_note(self.target, "picard-3", "pr", "y", "A.bar")
        notes = inbox.load_all(self.target)[0].notes
        self.assertEqual({n.lean_name for n in notes}, {"A.foo", "A.bar"})

    def test_issues_without_lean_name_append(self):
        # Free issues (no declaration) dedupe only on identical text.
        inbox.write_note(self.target, "picard-3", "issue", "bug one")
        inbox.write_note(self.target, "picard-3", "issue", "bug two")
        inbox.write_note(self.target, "picard-3", "issue", "bug one")  # dup → no-op
        notes = inbox.load_all(self.target)[0].notes
        self.assertEqual([n.payload for n in notes], ["bug one", "bug two"])
        self.assertTrue(all(n.type == "issue" for n in notes))

    def test_distinct_authors_distinct_files(self):
        inbox.write_note(self.target, "picard-3", "pr", "x", "A.foo")
        inbox.write_note(self.target, "ramanujan", "pr", "y", "A.foo")
        self.assertEqual(len(list(inbox.inbox_dir(self.target).glob("*.yaml"))), 2)
        self.assertEqual({a for a, _ in inbox.open_notes(self.target)}, {"picard-3", "ramanujan"})

    def test_set_status_and_open_filter(self):
        inbox.write_note(self.target, "picard-3", "pr", "x", "A.foo")
        self.assertEqual(len(inbox.open_notes(self.target)), 1)
        self.assertTrue(inbox.set_status(self.target, "picard-3", "A.foo", "accepted"))
        self.assertEqual(inbox.open_notes(self.target), [])

    def test_resolve_by_id(self):
        # The universal handle: works for issues that carry no lean_name.
        inbox.write_note(self.target, "picard-3", "issue", "something is off")
        _author, note = inbox.open_notes(self.target)[0]
        self.assertTrue(inbox.set_status_by_id(self.target, note.id, "declined"))
        self.assertEqual(inbox.open_notes(self.target), [])
        self.assertFalse(inbox.set_status_by_id(self.target, "deadbeef", "accepted"))

    def test_renote_resets_status_to_open(self):
        inbox.write_note(self.target, "picard-3", "pr", "x", "A.foo")
        inbox.set_status(self.target, "picard-3", "A.foo", "declined")
        inbox.write_note(self.target, "picard-3", "pr", "reconsider please", "A.foo")
        self.assertEqual(len(inbox.open_notes(self.target)), 1)

    def test_author_filename_sanitized(self):
        inbox.write_note(self.target, "parent/child", "pr", "x", "A.foo")
        files = [f.name for f in inbox.inbox_dir(self.target).glob("*.yaml")]
        self.assertEqual(files, ["parent-child.yaml"])
        # still round-trips the original author from the `from:` field
        self.assertEqual(inbox.load_all(self.target)[0].author, "parent/child")

    def test_no_inbox_is_empty(self):
        self.assertEqual(inbox.load_all(self.target), [])
        self.assertEqual(inbox.open_notes(self.target), [])


if __name__ == "__main__":
    unittest.main()
