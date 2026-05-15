"""Tests for the per-iteration dispatch semaphore + hierarchical Subagent plumbing.

Covers:

* SlotPool acquire/release/timeout/stale-lease semantics
* Concurrent acquire under multiple threads respects the cap
* Domain-coverage helpers in ``archon.subagents.base``
* Validation in ``Subagent`` actually rejects out-of-domain children
  by reading dispatch.jsonl
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from archon.dispatch import (
    MAX_PARALLEL_ENV_VAR,
    SLOTS_ENV_VAR,
    SlotPool,
)
from archon.subagents.base import (
    ROOT_PARENT_SLUG,
    Subagent,
    SubagentDescriptor,
    SubagentResult,
    WriteDomainViolation,
    _append_dispatch_jsonl,
    _domain_covers,
    _glob_covers,
)


def _fake_descriptor(name: str = "fake") -> SubagentDescriptor:
    """Synthetic descriptor for the validation-only tests below."""
    return SubagentDescriptor(name=name, description="test fake", prompt_body="")


# ── SlotPool ─────────────────────────────────────────────────────────


class SlotPoolTest(unittest.TestCase):
    def test_init_creates_slots(self):
        with tempfile.TemporaryDirectory() as d:
            pool = SlotPool.init(Path(d), 3)
            self.assertEqual(pool.free_count(), 3)
            self.assertEqual(pool.held_count(), 0)

    def test_init_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            SlotPool.init(Path(d), 2)
            pool = SlotPool.init(Path(d), 2)  # second init at same size
            self.assertEqual(pool.free_count(), 2)

    def test_init_clamps_to_one(self):
        with tempfile.TemporaryDirectory() as d:
            pool = SlotPool.init(Path(d), 0)
            self.assertEqual(pool.free_count(), 1)

    def test_acquire_release_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            pool = SlotPool.init(Path(d), 2)
            h = pool.acquire()
            self.assertEqual(pool.free_count(), 1)
            self.assertEqual(pool.held_count(), 1)
            pool.release(h)
            self.assertEqual(pool.free_count(), 2)
            self.assertEqual(pool.held_count(), 0)

    def test_acquire_blocks_when_exhausted(self):
        with tempfile.TemporaryDirectory() as d:
            pool = SlotPool.init(Path(d), 1)
            pool.acquire()
            with self.assertRaises(TimeoutError):
                pool.acquire(timeout_s=0.3)

    def test_context_manager_releases_on_exception(self):
        with tempfile.TemporaryDirectory() as d:
            pool = SlotPool.init(Path(d), 1)
            try:
                with pool.slot():
                    raise RuntimeError("boom")
            except RuntimeError:
                pass
            # Slot must be released even though the body raised.
            self.assertEqual(pool.free_count(), 1)

    def test_concurrent_cap(self):
        """Peak concurrency must not exceed max_parallel under 10 workers."""
        with tempfile.TemporaryDirectory() as d:
            pool = SlotPool.init(Path(d), 3)
            peak = [0]
            current = [0]
            lock = threading.Lock()

            def worker():
                with pool.slot():
                    with lock:
                        current[0] += 1
                        if current[0] > peak[0]:
                            peak[0] = current[0]
                    time.sleep(0.05)
                    with lock:
                        current[0] -= 1

            threads = [threading.Thread(target=worker) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertLessEqual(peak[0], 3)
            self.assertEqual(pool.free_count(), 3)

    def test_stale_reap_on_init(self):
        """A held slot whose mtime is older than threshold is reclaimed."""
        with tempfile.TemporaryDirectory() as d:
            pool = SlotPool.init(Path(d), 2)
            h = pool.acquire()
            self.assertEqual(pool.held_count(), 1)

            # Backdate the slot's mtime past the stale threshold.
            old = time.time() - 7200
            os.utime(h, (old, old))

            # Re-init with a smaller threshold to force reap.
            pool2 = SlotPool.init(Path(d), 2, stale_after_s=1.0)
            self.assertEqual(pool2.free_count(), 2)
            self.assertEqual(pool2.held_count(), 0)

    def test_from_env_returns_none_when_unset(self):
        old = os.environ.pop(SLOTS_ENV_VAR, None)
        try:
            self.assertIsNone(SlotPool.from_env())
        finally:
            if old is not None:
                os.environ[SLOTS_ENV_VAR] = old

    def test_from_env_uses_env_var(self):
        with tempfile.TemporaryDirectory() as d:
            SlotPool.init(Path(d), 2)
            old = os.environ.get(SLOTS_ENV_VAR)
            os.environ[SLOTS_ENV_VAR] = d
            try:
                pool = SlotPool.from_env()
                self.assertIsNotNone(pool)
                self.assertEqual(pool.free_count(), 2)
            finally:
                if old is None:
                    os.environ.pop(SLOTS_ENV_VAR, None)
                else:
                    os.environ[SLOTS_ENV_VAR] = old


# ── domain-coverage helpers ─────────────────────────────────────────


class DomainCoverageTest(unittest.TestCase):
    def test_root_double_star_covers_all(self):
        self.assertTrue(_glob_covers("**", "Algebra/Foo.lean"))
        self.assertTrue(_glob_covers("**/*", "anywhere"))

    def test_directory_prefix_covers_files_and_subdirs(self):
        self.assertTrue(_glob_covers("Algebra/**", "Algebra/Foo.lean"))
        self.assertTrue(_glob_covers("Algebra/**", "Algebra/Sub/Bar.lean"))

    def test_disjoint_dirs_not_covered(self):
        self.assertFalse(_glob_covers("Algebra/**", "Picard/Foo.lean"))

    def test_exact_equality(self):
        self.assertTrue(_glob_covers("a/b.lean", "a/b.lean"))
        self.assertFalse(_glob_covers("a/b.lean", "a/c.lean"))

    def test_parent_unrestricted_covers_anything(self):
        self.assertTrue(_domain_covers([], ["anything", "anywhere"]))

    def test_child_subset_of_parent(self):
        self.assertTrue(
            _domain_covers(["Algebra/**"], ["Algebra/Foo.lean", "Algebra/Bar/**"])
        )

    def test_child_escapes_parent_rejected(self):
        self.assertFalse(_domain_covers(["Algebra/**"], ["Picard/Foo.lean"]))

    def test_partial_escape_rejected(self):
        """Even one escaping glob fails the family."""
        self.assertFalse(
            _domain_covers(["Algebra/**"], ["Algebra/Foo.lean", "Picard/Bar.lean"])
        )


# ── Subagent.run write-domain enforcement ───────────────────────────


def _make_fake_subagent(project: Path) -> Subagent:
    """Build a Subagent from a synthetic descriptor.

    The validation tests below never call ``run`` — they exercise
    ``_validate_write_domain`` and ``report_path`` directly, so the
    prompt body / model resolution don't actually matter. We bypass
    ``load_project_config`` by passing an explicit ``model``.
    """
    return Subagent(_fake_descriptor("fake"), project, model="test-model")


class WriteDomainValidationTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.project = Path(self._td.name)
        self.state = self.project / ".archon"
        self.iter_dir = self.state / "logs" / "iter-005"
        self.iter_dir.mkdir(parents=True)
        self.dispatch_log = self.iter_dir / "dispatch.jsonl"
        # Seed a parent record so children have something to subset against.
        _append_dispatch_jsonl(self.dispatch_log, {
            "event": "dispatch_start",
            "role": "coordinator",
            "slug": "parent",
            "parent_slug": ROOT_PARENT_SLUG,
            "write_domain": ["Algebra/**"],
        })

    def tearDown(self):
        self._td.cleanup()

    def test_child_in_domain_passes(self):
        sub = _make_fake_subagent(self.project)
        log_base = self.iter_dir / "fake-child"
        # Triggers validation only (we patch out actual run to avoid claude).
        sub._validate_write_domain(
            self.dispatch_log, "parent", ["Algebra/Foo.lean"],
        )  # no exception

    def test_child_out_of_domain_raises(self):
        sub = _make_fake_subagent(self.project)
        with self.assertRaises(WriteDomainViolation):
            sub._validate_write_domain(
                self.dispatch_log, "parent", ["Picard/Foo.lean"],
            )

    def test_root_parent_unrestricted(self):
        """ROOT_PARENT_SLUG bypasses domain enforcement."""
        sub = _make_fake_subagent(self.project)
        # No exception even though there's no parent record matching _root.
        sub._validate_write_domain(
            self.dispatch_log, ROOT_PARENT_SLUG, ["anything/**"],
        )

    def test_missing_parent_record_raises(self):
        sub = _make_fake_subagent(self.project)
        with self.assertRaises(WriteDomainViolation):
            sub._validate_write_domain(
                self.dispatch_log, "nonexistent-parent", ["Anything/**"],
            )

    def test_empty_child_domain_skips_check(self):
        """A child that declares no domain is implicitly trusted within parent."""
        sub = _make_fake_subagent(self.project)
        sub._validate_write_domain(self.dispatch_log, "parent", [])

    def test_report_path_layout_root(self):
        sub = _make_fake_subagent(self.project)
        p = sub.report_path("mychild", parent_slug=ROOT_PARENT_SLUG)
        self.assertTrue(str(p).endswith("/task_results/fake-mychild.md"))

    def test_report_path_layout_nested(self):
        sub = _make_fake_subagent(self.project)
        p = sub.report_path("mychild", parent_slug="parent")
        self.assertTrue(str(p).endswith("/task_results/parent/fake-mychild.md"))


if __name__ == "__main__":
    unittest.main()
