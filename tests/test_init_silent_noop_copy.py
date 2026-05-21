"""Tests for the "silent no-op on identical overwrite" behavior.

``archon init`` in overwrite mode used to log
``Overwriting existing file: <name>`` for every bundled template
file — even when the destination was byte-identical to the source.
That misled users into thinking a destructive replacement happened on
every re-init. ``copy_file`` now short-circuits when content matches.
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from archon.commands.init.utils import _files_equal, copy_file


class FilesEqualTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(_rmtree, self._tmp)
        self.root = Path(self._tmp)

    def _write(self, name: str, content: bytes) -> Path:
        p = self.root / name
        p.write_bytes(content)
        return p

    def test_byte_identical_returns_true(self):
        a = self._write("a", b"hello world")
        b = self._write("b", b"hello world")
        self.assertTrue(_files_equal(a, b))

    def test_different_size_returns_false(self):
        a = self._write("a", b"hello")
        b = self._write("b", b"hello world")
        self.assertFalse(_files_equal(a, b))

    def test_same_size_different_content_returns_false(self):
        a = self._write("a", b"abcde")
        b = self._write("b", b"abcdf")
        self.assertFalse(_files_equal(a, b))

    def test_missing_file_returns_false(self):
        a = self._write("a", b"x")
        self.assertFalse(_files_equal(a, self.root / "nope"))


class CopyFileSilentNoopTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(_rmtree, self._tmp)
        self.root = Path(self._tmp)
        self.src = self.root / "src.txt"
        self.dst = self.root / "dst.txt"
        self.src.write_text("hello\n", encoding="utf-8")

    def _capture(self, fn) -> str:
        # log.warn goes through rich's Console which writes to stdout
        # by default; capture both streams to be safe.
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            fn()
        return buf_out.getvalue() + buf_err.getvalue()

    def test_identical_overwrite_is_silent_noop(self):
        self.dst.write_text("hello\n", encoding="utf-8")
        before = self.dst.stat().st_mtime_ns
        output = self._capture(
            lambda: copy_file(self.src, self.dst, overwrite=True),
        )
        # No warning.
        self.assertNotIn("Overwriting", output)
        # File not rewritten (mtime untouched).
        self.assertEqual(self.dst.stat().st_mtime_ns, before)

    def test_different_overwrite_warns_and_replaces(self):
        self.dst.write_text("DIFFERENT\n", encoding="utf-8")
        output = self._capture(
            lambda: copy_file(self.src, self.dst, overwrite=True),
        )
        self.assertIn("Overwriting", output)
        self.assertEqual(self.dst.read_text(encoding="utf-8"), "hello\n")

    def test_missing_destination_copies_silently(self):
        # No "Overwriting" warning when the destination doesn't exist
        # in the first place.
        output = self._capture(
            lambda: copy_file(self.src, self.dst, overwrite=False),
        )
        self.assertNotIn("Overwriting", output)
        self.assertEqual(self.dst.read_text(encoding="utf-8"), "hello\n")


def _rmtree(path: str) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
